"""identity 表定义（设计文档 §14、§10）。

表名与列名是跨模块契约：其他模块只用字符串外键引用这些表，
`app/core/deps.py` 也直接按 `token_digest` / `expires_at` / `revoked_at` / `user_id` / `User.status`
查询会话与用户，因此这些列名不得改动（见 docs/ARCHITECTURE.md §3）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AccountStatus,
    AuthTokenPurpose,
    IdentityKind,
    IdentityStatus,
    Role,
)
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["User", "UserProfile", "UserIdentity", "Session", "AuthToken"]


def _enum_values(enum_cls: type[Enum]) -> str:
    """生成 CheckConstraint 用的字面量列表，状态列的取值以枚举为唯一来源。"""
    return ", ".join(f"'{member.value}'" for member in enum_cls)


class User(Base, UUIDPrimaryKey, Timestamped):
    """账号状态与登录凭据。昵称等展示信息不放这里，避免被当作可随意覆盖的快照。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN ({_enum_values(Role)})", name="role"),
        CheckConstraint(f"status IN ({_enum_values(AccountStatus)})", name="status"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.USER.value)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=AccountStatus.PENDING_VERIFICATION.value,
        index=True,
    )
    # 只表示"当时控制该邮箱"，不等于官方学生身份认证（§14.1）。
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # selectin 预加载：display_name_of(user) 与 /me 需要在不触发异步懒加载的前提下读取档案。
    profile: Mapped["UserProfile | None"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class UserProfile(Base, UUIDPrimaryKey, Timestamped):
    """昵称、头像与可选学习偏好。"""

    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 学院/专业归属 catalog 模块，这里只保存引用，不复制目录事实。
    college_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True
    )
    major_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("majors.id"), nullable=True)
    enrollment_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    user: Mapped["User"] = relationship(back_populates="profile")


class UserIdentity(Base, UUIDPrimaryKey, Timestamped):
    """身份声明与验证来源、时间。邮箱验证只证明当时控制该邮箱（§14.1）。"""

    __tablename__ = "user_identities"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_enum_values(IdentityKind)})", name="kind"),
        CheckConstraint(f"status IN ({_enum_values(IdentityStatus)})", name="status"),
        # 同一标识（如邮箱）不能被两个账号同时声明。
        UniqueConstraint("kind", "identifier", name="uq_user_identities_kind_identifier"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    identifier: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IdentityStatus.PENDING.value
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(Base, UUIDPrimaryKey, Timestamped):
    """可撤销会话事实，统一存放在 PostgreSQL，Redis 只做缓存（§14.2）。"""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 只存摘要；明文令牌仅下发到 HttpOnly Cookie（§14.2）。
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuthToken(Base, UUIDPrimaryKey, Timestamped):
    """邮箱验证与找回密码的一次性令牌：有期限、可撤销、用后即焚（§14.2）。"""

    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(f"purpose IN ({_enum_values(AuthTokenPurpose)})", name="purpose"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
