"""案件、侵权通知与反通知表（设计文档 §13、§15.5、§16.2）。

三类案件分别管理，结论互不自动替代：内容下架不自动等于收入没收，
审核通过不自动等于版权无争议。`case_events` 追加写，保留转送与送达事实。

跨模块外键只写字符串表名（§2.2），不导入对方模型类。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    CaseEventType,
    CaseMeasureKind,
    CaseMeasureStatus,
    CaseStatus,
    CaseSubjectType,
    CaseType,
    RightsNoticeStatus,
)
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "Case",
    "CaseEvent",
    "CaseMeasure",
    "RightsNotice",
    "CounterStatement",
]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """生成状态列 CheckConstraint 的 SQL（§4：String 存枚举值 + CheckConstraint）。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class Case(Base, UUIDPrimaryKey, Timestamped):
    """案件主体。三类案件（交付争议／收益异议／一般申诉／侵权通知）共用一张表。

    `subject_type`＋`subject_id` 明确指向对象（逐对象作用域过滤，不用任意多态字符串）；
    案件状态与措施、送达、期限是独立维度，不用单一字段承载全过程（§13.1、§14.1）。
    """

    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(_in("case_type", CaseType), name="case_type"),
        CheckConstraint(_in("subject_type", CaseSubjectType), name="subject_type"),
        CheckConstraint(_in("state", CaseStatus), name="state"),
        Index("ix_cases_applicant_state", "applicant_id", "state"),
        Index("ix_cases_type_state", "case_type", "state"),
    )

    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CaseStatus.OPEN.value
    )
    # 记录提交时适用的平台规则版本，规则可查询历史版本（§13.5）；未配置时留空。
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handler_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseEvent(Base, UUIDPrimaryKey, Timestamped):
    """案件事件（追加写）。保留转送与送达事实，不覆盖历史（§15.5）。

    转送/送达时间写入 `served_at`，适用期限写入 `deadline_at`；
    期限按适用法定路径记录，不把所有案件统一写成同一数值（§13.3）。
    """

    __tablename__ = "case_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", CaseEventType), name="event_type"),
        Index("ix_case_events_case_created", "case_id", "created_at"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cases.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseMeasure(Base, UUIDPrimaryKey, Timestamped):
    """必要措施。措施可解除，记录影响范围与依据（§13.3、§13.4）。

    商业化关闭时只记录措施行，不改动任何资金；解除措施不机械自动重新上架。
    """

    __tablename__ = "case_measures"
    __table_args__ = (
        CheckConstraint(_in("target_type", CaseSubjectType), name="target_type"),
        CheckConstraint(_in("measure", CaseMeasureKind), name="measure"),
        CheckConstraint(_in("status", CaseMeasureStatus), name="status"),
        Index("ix_case_measures_case", "case_id"),
        Index("ix_case_measures_target_status", "target_type", "target_id", "status"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cases.id"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    measure: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CaseMeasureStatus.ACTIVE.value
    )
    # 冻结/停售等措施涉及的金额（整数分）；不涉及金额时为 NULL。
    amount_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )


class RightsNotice(Base, UUIDPrimaryKey, Timestamped):
    """侵权通知（§13.3）。无需购买即可提交。

    `claimant_name`／`claimant_contact`／`rights_basis_ref` 属敏感身份与联系信息，
    列表与他人视角一律不返回；仅在受理人员履职时提供。
    """

    __tablename__ = "rights_notices"
    __table_args__ = (
        CheckConstraint(_in("status", RightsNoticeStatus), name="status"),
        Index("ix_rights_notices_case", "case_id"),
        Index("ix_rights_notices_status", "status"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cases.id"), nullable=False, index=True
    )
    claimant_name: Mapped[str] = mapped_column(String(300), nullable=False)
    claimant_contact: Mapped[str] = mapped_column(String(300), nullable=False)
    rights_basis_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    requested_action: Mapped[str] = mapped_column(String(300), nullable=False)
    # 真实性声明的条款版本；声明本身只记录版本与时间，不保存完整文本副本。
    statement_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RightsNoticeStatus.RECEIVED.value
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CounterStatement(Base, UUIDPrimaryKey, Timestamped):
    """不存在侵权声明（反通知）。仅被通知方或合法代理可提交（§13.3）。"""

    __tablename__ = "counter_statements"
    __table_args__ = (
        Index("ix_counter_statements_notice", "rights_notice_id"),
    )

    rights_notice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("rights_notices.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cases.id"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
