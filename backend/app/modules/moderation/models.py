"""审核、举报与审计表（设计文档 §13.2、§13.3、§18、§27、§29）。

本模块拥有 `reviews`、`moderation_decisions`、`reports`、`report_events`、`audit_logs`。

约定：
- 跨模块外键只写字符串表名（`docs/ARCHITECTURE.md` §2.2），不导入对方模型类。
- 状态列以 `String` 存 `app.core.enums` 的枚举值，并配 `CheckConstraint`（§13）。
- 决定、工单事件与审计为追加写入，不提供原地覆盖；因此只有 `created_at`。
  `reviews` 与 `reports` 可流转，使用 `Timestamped`（created_at + updated_at）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.core.enums import ModerationDecisionType, ReportStatus, ReviewStatus
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "Review",
    "ModerationDecision",
    "Report",
    "ReportEvent",
    "AuditLog",
]


class StringUUID(TypeDecorator):
    """以 VARCHAR 存储的标识列。

    `app.core.audit.record_audit` 会直接把 `uuid.UUID` 作为 `object_id` 传入，
    而列契约要求为 String。这里在绑定阶段统一转成字符串，DDL 仍是 VARCHAR。
    """

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return str(value)


def _enum_check(column: str, enum_cls: type, name: str) -> CheckConstraint:
    """由枚举值生成 CheckConstraint，避免字面量与 app.core.enums 漂移。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class CreatedAt:
    """追加写入表的创建时间。审计与事件记录不提供修改时间（§27）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class Review(Base, UUIDPrimaryKey, Timestamped):
    """一次针对明确版本快照的审核任务（§13.2、§16）。

    同一版本不产生多个有效审核任务（§29「作者重复点击提交」）：
    以 `uq_reviews_active_version` 局部唯一索引约束，只允许一份 draft/pending 任务。
    终态（approved/rejected）保留为历史证据，不再占用该约束。
    """

    __tablename__ = "reviews"
    __table_args__ = (
        _enum_check("status", ReviewStatus, "status_valid"),
        Index(
            "uq_reviews_active_version",
            "version_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'pending')"),
        ),
        # §15.2：审核队列按审核状态与提交时间建立索引与排序。
        Index("ix_reviews_status_submitted_at", "status", "submitted_at"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    submitter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReviewStatus.PENDING.value, index=True
    )
    # 审批对象包含完整快照，快照标识用于过期页面提交的条件更新（§12.1、§13.3）。
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assigned_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModerationDecision(Base, UUIDPrimaryKey, CreatedAt):
    """审核决定。决定不可原地覆盖，只追加（§15.1、§13.3）。"""

    __tablename__ = "moderation_decisions"
    __table_args__ = (
        _enum_check("decision", ModerationDecisionType, "decision_valid"),
        Index("ix_moderation_decisions_review_created", "review_id", "created_at"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reviews.id"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False)


class Report(Base, UUIDPrimaryKey, Timestamped):
    """举报工单（§13.2、§18）。

    举报有独立工单，不把资源直接改成一个含义混杂的 reported 状态；
    受理举报不等于认定违规。
    """

    __tablename__ = "reports"
    __table_args__ = (
        _enum_check("status", ReportStatus, "status_valid"),
        Index("ix_reports_status_created_at", "status", "created_at"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=True, index=True
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    reason_code: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReportStatus.OPEN.value, index=True
    )


class ReportEvent(Base, UUIDPrimaryKey, CreatedAt):
    """投诉工单流转记录（§18）：记录对象版本、理由、证据、处置与通知。

    临时措施与最终认定的区别通过 `evidence.interim` 与 `note` 明确记录。
    """

    __tablename__ = "report_events"
    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('open', 'triaged', 'investigating', 'resolved', 'dismissed', 'appealed')",
            name="from_status_valid",
        ),
        _enum_check("to_status", ReportStatus, "to_status_valid"),
        Index("ix_report_events_report_created", "report_id", "created_at"),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class AuditLog(Base, UUIDPrimaryKey, CreatedAt):
    """关键操作审计（§27）。

    列名由 `app.core.audit.record_audit` 精确构造，不得改名。
    不记录密码、会话令牌、签名 URL 或完整文件正文（scrub 已过滤）。
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_object", "object_type", "object_id"),
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(60), nullable=False)
    object_id: Mapped[str | None] = mapped_column(StringUUID, nullable=True)
    object_version: Mapped[str | None] = mapped_column(StringUUID, nullable=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
