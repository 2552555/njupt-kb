"""creator 表定义（设计文档 §24.1、§10；用户权责 §3.4、§14.1）。

`creator_profiles`：贡献者公开资料，与 identity 的账号档案分离。
`contributions`：获审通过的原创或授权资料、修订、被采纳纠错和解决需求的贡献记录。
`contributor_applications`：专业贡献者入驻申请与审核结论（用户权责 §3.4）。

贡献分用于展示与运营，不承诺兑换现金（§24.1）；其计算规则待确认（D16），
本版默认 0，不硬编码任何示例公式。

跨模块外键只引用字符串表名（docs/ARCHITECTURE.md §2.2）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ContributionKind, ContributorOnboardingStatus
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["CreatorProfile", "Contribution", "ContributorApplication"]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class CreatorProfile(Base, UUIDPrimaryKey, Timestamped):
    """贡献者公开资料（§24.1）。与普通账号档案分离，一个账号至多一份。"""

    __tablename__ = "creator_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_creator_profiles_user_id"),
        Index("ix_creator_profiles_public", "is_public"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    headline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Contribution(Base, UUIDPrimaryKey, Timestamped):
    """一条贡献记录（§24.1）。

    有效贡献 ≠ 授权链接次数：自下载、重复刷取、无权传播资料不得机械计为收益贡献，
    由服务层在写入前判定（见 `service.record_contribution`）。
    `(user_id, kind, reference_type, reference_id)` 唯一，同一业务事实不重复计贡献。
    """

    __tablename__ = "contributions"
    __table_args__ = (
        CheckConstraint(_in("kind", ContributionKind), name="kind_valid"),
        UniqueConstraint(
            "user_id",
            "kind",
            "reference_type",
            "reference_id",
            name="uq_contributions_business",
        ),
        Index("ix_contributions_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 业务引用：资源版本、纠错工单或需求单等。
    reference_type: Mapped[str] = mapped_column(String(60), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 展示/运营用贡献分；公式待确认（D16），默认 0，不构成现金负债（§24.1、§5.10）。
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ContributorApplication(Base, UUIDPrimaryKey, Timestamped):
    """专业贡献者入驻申请（用户权责 §3.4、§14.1）。

    普通用户可直接投稿免费内容；只有希望有偿销售时才需要入驻。入驻表示经认可的
    长期专业贡献关系，不代表投稿免审或分成必然更高（§3.4）。申请与结论只追加保留，
    状态机为 `applied → under_review → active/rejected`，生效后可暂停、恢复或退出。
    暂停及退出撤销受托管理范围，保留本人内容、既有合同、收益与购买权益。
    """

    __tablename__ = "contributor_applications"
    __table_args__ = (
        CheckConstraint(_in("status", ContributorOnboardingStatus), name="status_valid"),
        # 支持「某用户是否存在未关闭申请」的按用户维度查询；`(user_id, status)` 不要求唯一。
        Index("ix_contributor_applications_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ContributorOnboardingStatus.APPLIED.value
    )
    # 能力说明与权利材料引用：说明「能做什么」与「凭什么」。
    capability_summary: Mapped[str] = mapped_column(Text, nullable=False)
    rights_materials_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    # 身份核验、签约主体引用：审核与签约阶段回填，提交时可为空。
    verification_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contract_party_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
