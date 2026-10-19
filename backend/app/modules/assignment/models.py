"""assignment 表定义（用户权责体系 §15.2）。

跨模块外键只引用字符串表名（docs/ARCHITECTURE.md §2.2），不导入其他模块的模型类。

两条不变量：
- 权限分配只允许 `app.core.permissions.ALL_PERMISSIONS` 中存在的权限名（服务层校验）；
- 资源分配必须且只能指向一个目标（资源或课程），不接受任意多态字符串（§15.2）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AdminPermissionScope, AssignmentCapability
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["AdminPermissionAssignment", "ResourceAssignment"]


def _in(column: str, enum_cls: type) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class AdminPermissionAssignment(Base, UUIDPrimaryKey, Timestamped):
    """按职责追加的系统管理员权限（用户权责体系 §3.1、§15.2）。

    `(user_id, permission)` 唯一：同一权限同时只有一条有效记录，撤销走 `revoked_at`，
    不物理删除，保留授权与撤销审计。`valid_until` 为空表示不设到期时间。
    """

    __tablename__ = "admin_permission_assignments"
    __table_args__ = (
        CheckConstraint(_in("scope", AdminPermissionScope), name="scope_valid"),
        UniqueConstraint("user_id", "permission", name="uq_admin_permissions_user_permission"),
        Index("ix_admin_permissions_user_active", "user_id", "revoked_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AdminPermissionScope.GLOBAL.value
    )
    # 作用范围引用：按课程目录或按资源授予时填写；全局权限为空。
    scope_catalog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    scope_resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResourceAssignment(Base, UUIDPrimaryKey, Timestamped):
    """资料管理员的作用范围（用户权责体系 §3.3、§15.2）。

    `(user_id, resource_id, course_id, capability)` 唯一。目标必须且只能有一个：
    指向资源或课程，由 CheckConstraint 保证，避免「任意多态字符串」绕过对象鉴权。
    `revoked_at` 非空即失效；范围变更立即停止新访问与新签名链接（§3.3）。
    """

    __tablename__ = "resource_assignments"
    __table_args__ = (
        CheckConstraint(_in("capability", AssignmentCapability), name="capability_valid"),
        CheckConstraint(
            "(resource_id IS NOT NULL AND course_id IS NULL)"
            " OR (resource_id IS NULL AND course_id IS NOT NULL)",
            name="exactly_one_target",
        ),
        UniqueConstraint(
            "user_id",
            "resource_id",
            "course_id",
            "capability",
            name="uq_resource_assignments_scope",
        ),
        Index("ix_resource_assignments_user_active", "user_id", "revoked_at"),
        Index("ix_resource_assignments_resource", "resource_id"),
        Index("ix_resource_assignments_course", "course_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resources.id"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id"), nullable=True
    )
    capability: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AssignmentCapability.MAINTAIN.value
    )
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
