"""assignment 请求/响应模型（用户权责体系 §3、§15.2）。

请求侧强制 `reason` 1–255 字；`permission`/`scope`/`capability` 的取值以
`app.core.enums` 为唯一来源。响应侧只暴露授权与撤销所需字段，不返回任何
他人隐私数据（`/me/assignments` 也只描述本人）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AdminPermissionScope, AssignmentCapability


class PermissionAssignmentCreate(BaseModel):
    """全站职责授权；对象维护范围由 ResourceAssignment 单独管理。"""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    permission: str = Field(min_length=1, max_length=64)
    valid_until: datetime | None = None
    reason: str = Field(min_length=1, max_length=255)


class PermissionAssignmentRevokeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class PermissionAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    permission: str
    scope: AdminPermissionScope
    scope_catalog_id: uuid.UUID | None
    scope_resource_id: uuid.UUID | None
    valid_until: datetime | None
    granted_by: uuid.UUID | None
    reason: str | None
    revoked_at: datetime | None
    revoked_reason: str | None
    created_at: datetime


class ResourceAssignmentCreate(BaseModel):
    """资料管理员的作用范围；`resource_id` 与 `course_id` 二选一（§3.3）。"""

    user_id: uuid.UUID
    resource_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    capability: AssignmentCapability = AssignmentCapability.MAINTAIN
    effective_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=255)


class ResourceAssignmentRevokeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class ResourceAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    resource_id: uuid.UUID | None
    course_id: uuid.UUID | None
    capability: AssignmentCapability
    effective_at: datetime | None
    assigned_by: uuid.UUID | None
    reason: str | None
    revoked_at: datetime | None
    revoked_reason: str | None
    created_at: datetime


class MyAssignmentsOut(BaseModel):
    """本人当前有效的分配与权限；只描述本人，不泄露他人（§3.3）。"""

    permissions: list[PermissionAssignmentOut]
    resource_assignments: list[ResourceAssignmentOut]
