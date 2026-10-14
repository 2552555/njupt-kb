"""creator 表定义（设计文档 §24.1、§10）。

`creator_profiles`：贡献者公开资料，与 identity 的账号档案分离。
`contributions`：获审通过的原创或授权资料、修订、被采纳纠错和解决需求的贡献记录。

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

from app.core.enums import ContributionKind
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["CreatorProfile", "Contribution"]


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
