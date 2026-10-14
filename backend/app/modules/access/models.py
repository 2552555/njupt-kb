"""access 模块表定义（设计文档 §15.1、§15.2）。

所有权：访问策略、权益、下载授权、额度占用、预览凭证。
跨模块外键只引用字符串表名（docs/ARCHITECTURE.md §2.2），不导入其他模块的模型类。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Capability, EntitlementSource, GrantStatus, ScopeType
from app.db import Base, Revisioned, Timestamped, UUIDPrimaryKey

__all__ = [
    "AccessPolicy",
    "DownloadGrant",
    "DownloadQuotaUsage",
    "Entitlement",
    "PreviewGrant",
]


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """生成受约束枚举的 CheckConstraint 表达式（§10：状态用受约束枚举）。"""
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


_SCOPE_VALUES = tuple(scope.value for scope in ScopeType)
_CAPABILITY_VALUES = tuple(capability.value for capability in Capability)
_SOURCE_VALUES = tuple(source.value for source in EntitlementSource)
_GRANT_STATUS_VALUES = tuple(status.value for status in GrantStatus)


class AccessPolicy(Base, UUIDPrimaryKey, Timestamped, Revisioned):
    """资源访问范围（§15.1）。

    范围是 public / authenticated / verified_school / explicit_grant / private 的明确集合，
    首期只表达固定可理解的范围组合，不实现任意条件表达式。未知策略默认拒绝。
    一个资源一条策略；resources.access_policy_id 由 content 拥有，本模块不改写它。
    """

    __tablename__ = "access_policies"
    __table_args__ = (
        UniqueConstraint("resource_id", name="uq_access_policies_resource_id"),
        CheckConstraint(_in_list("metadata_scope", _SCOPE_VALUES), name="metadata_scope_valid"),
        CheckConstraint(_in_list("preview_scope", _SCOPE_VALUES), name="preview_scope_valid"),
        CheckConstraint(_in_list("original_scope", _SCOPE_VALUES), name="original_scope_valid"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id"), nullable=False
    )
    metadata_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScopeType.PUBLIC.value
    )
    preview_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScopeType.AUTHENTICATED.value
    )
    original_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScopeType.EXPLICIT_GRANT.value
    )


class Entitlement(Base, UUIDPrimaryKey, Timestamped):
    """访问权益（§15.1）。

    表示某用户对某资源、版本或课程的访问资格；来源可为免费开放、作者、管理员授予，
    P3 增加订单与订阅。不把支付状态直接复制成永久权限。
    """

    __tablename__ = "entitlements"
    __table_args__ = (
        CheckConstraint(_in_list("scope_type", _SCOPE_VALUES), name="scope_type_valid"),
        CheckConstraint(_in_list("capability", _CAPABILITY_VALUES), name="capability_valid"),
        CheckConstraint(_in_list("source_type", _SOURCE_VALUES), name="source_type_valid"),
        Index("ix_entitlements_user_scope", "user_id", "scope_type", "scope_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DownloadGrant(Base, UUIDPrimaryKey, Timestamped):
    """下载授权（§15.1、§17.2）。

    绑定用户、资源、版本与文件；至少包含权益来源、幂等键、状态、创建时间与授权期限。
    (user_id, idempotency_key) 唯一：同一幂等键不重复计次（§15.2、A10）。
    """

    __tablename__ = "download_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_download_grants_user_key"),
        CheckConstraint(_in_list("status", _GRANT_STATUS_VALUES), name="status_valid"),
        Index("ix_download_grants_resource_created", "resource_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_files.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entitlement_ref: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entitlements.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=GrantStatus.ISSUED.value
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DownloadQuotaUsage(Base, UUIDPrimaryKey, Timestamped):
    """下载额度占用（§15.1、§29）。

    同一授权最多占用一次（grant_id 唯一）；额度变更以追加记录可追溯，不原地改写历史。
    首期不接现金与积分（D05），额度仅作反滥用粗粒度限制。
    """

    __tablename__ = "download_quota_usage"
    __table_args__ = (
        UniqueConstraint("grant_id", name="uq_download_quota_usage_grant_id"),
        Index("ix_download_quota_usage_user_period", "user_id", "period_start"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("download_grants.id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PreviewGrant(Base, UUIDPrimaryKey, Timestamped):
    """预览访问凭证（§17.1）。

    预览只提供派生产物，不向低权限用户传输完整原件；预览遵守来源内容的访问范围。
    """

    __tablename__ = "preview_grants"
    __table_args__ = (
        CheckConstraint(_in_list("status", _GRANT_STATUS_VALUES), name="status_valid"),
        Index("ix_preview_grants_resource_created", "resource_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resource_files.id"), nullable=True
    )
    derivative_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=GrantStatus.ISSUED.value
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
