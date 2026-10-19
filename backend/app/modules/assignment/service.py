"""assignment 业务规则（用户权责体系 §3、§15.2）。

两条职责：
1. 管理接口的业务规则（授予、撤销、查询），只在此处写审计与域事件；
2. 资源级鉴权函数（`active_assignment_*`、`can_manage_resource`），供 content 等
   模块在函数体内局部导入复用，用于「可对哪一个对象做」的判定。

`owner`（内容管理关系）与 `resource_assignment`（明确分配）只**追加**判定，
不改变既有审核权限语义：审核仍由 `RESOURCE_REVIEW` 决定（§3.3）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.enums import AssignmentCapability, DomainEventType
from app.core.errors import Conflict, InvalidInput, NotFound
from app.core.events import emit_event
from app.core.pagination import (
    CursorPage,
    encode_cursor,
    normalize_page_size,
    seek_descending,
)
from app.core.permissions import ALL_PERMISSIONS
from app.modules.assignment.models import AdminPermissionAssignment, ResourceAssignment
from app.modules.assignment.schemas import (
    MyAssignmentsOut,
    PermissionAssignmentCreate,
    PermissionAssignmentOut,
    ResourceAssignmentCreate,
    ResourceAssignmentOut,
)

__all__ = [
    "create_permission_assignment",
    "list_permission_assignments",
    "revoke_permission_assignment",
    "create_resource_assignment",
    "list_resource_assignments",
    "revoke_resource_assignment",
    "my_assignments",
    "active_assignment_resource_ids",
    "active_assignment_course_ids",
    "can_manage_resource",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_str(value) -> str:
    return getattr(value, "value", value)


def _permission_out(row: AdminPermissionAssignment) -> PermissionAssignmentOut:
    return PermissionAssignmentOut(
        id=row.id,
        user_id=row.user_id,
        permission=row.permission,
        scope=row.scope,
        scope_catalog_id=row.scope_catalog_id,
        scope_resource_id=row.scope_resource_id,
        valid_until=row.valid_until,
        granted_by=row.granted_by,
        reason=row.reason,
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
        created_at=row.created_at,
    )


def _resource_out(row: ResourceAssignment) -> ResourceAssignmentOut:
    return ResourceAssignmentOut(
        id=row.id,
        user_id=row.user_id,
        resource_id=row.resource_id,
        course_id=row.course_id,
        capability=row.capability,
        effective_at=row.effective_at,
        assigned_by=row.assigned_by,
        reason=row.reason,
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
        created_at=row.created_at,
    )


# --------------------------------------------------------------------------- #
# 系统管理员权限分配（§3.1、§15.2）
# --------------------------------------------------------------------------- #
async def create_permission_assignment(
    db: AsyncSession,
    *,
    actor,
    payload: PermissionAssignmentCreate,
    request_id: str | None,
) -> AdminPermissionAssignment:
    """按职责追加一项管理权限；`permission` 必须属于 `ALL_PERMISSIONS`。"""
    if payload.permission not in ALL_PERMISSIONS:
        raise InvalidInput(
            "未知的权限名",
            details={"reason": "unknown_permission", "permission": payload.permission},
        )
    from app.modules.identity.service import get_user

    target = await get_user(db, payload.user_id)  # 找不到抛 NotFound
    from app.modules.identity.models import User
    await db.execute(select(User.id).where(User.id == target.id).with_for_update())

    # 表上 `(user_id, permission)` 唯一且覆盖已撤销记录，因此复用同一行重新授权，
    # 而不是插入新行：既避免唯一冲突，也保留完整的授权—撤销历史（§15.2 授权与撤销审计）。
    existing = (
        await db.execute(
            select(AdminPermissionAssignment).where(
                AdminPermissionAssignment.user_id == target.id,
                AdminPermissionAssignment.permission == payload.permission,
            )
        )
    ).scalar_one_or_none()

    before = None
    if existing is None:
        row = AdminPermissionAssignment(user_id=target.id, permission=payload.permission)
        db.add(row)
    else:
        row = existing
        before = {
            "scope": row.scope,
            "valid_until": row.valid_until.isoformat() if row.valid_until else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        }

    # 0.0.2 用户决定：后台职责覆盖全站；旧范围记录不自动升级，须显式重新授予。
    row.scope = "global"
    row.scope_catalog_id = None
    row.scope_resource_id = None
    row.valid_until = payload.valid_until
    row.granted_by = actor.id
    row.reason = payload.reason
    row.revoked_at = None
    row.revoked_by = None
    row.revoked_reason = None
    await db.flush()

    await record_audit(
        db,
        action="assignment.permission_grant",
        object_type="admin_permission_assignment",
        object_id=row.id,
        actor_id=actor.id,
        before=before,
        after={
            "user_id": str(row.user_id),
            "permission": row.permission,
            "scope": row.scope,
            "valid_until": row.valid_until.isoformat() if row.valid_until else None,
        },
        reason=payload.reason,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.ASSIGNMENT_CHANGED,
        subject_type="admin_permission_assignment",
        subject_id=row.id,
        payload={
            "user_id": str(row.user_id),
            "permission": row.permission,
            "action": "granted",
        },
    )
    return row


async def list_permission_assignments(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> CursorPage[PermissionAssignmentOut]:
    size = normalize_page_size(limit)
    stmt = select(AdminPermissionAssignment)
    if user_id is not None:
        stmt = stmt.where(AdminPermissionAssignment.user_id == user_id)
    stmt = seek_descending(
        db, stmt, AdminPermissionAssignment.created_at, AdminPermissionAssignment.id, cursor
    )
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"ts": last.created_at.isoformat(), "id": str(last.id)})
    return CursorPage[PermissionAssignmentOut](
        items=[_permission_out(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def revoke_permission_assignment(
    db: AsyncSession,
    *,
    actor,
    assignment_id: uuid.UUID,
    reason: str,
    request_id: str | None,
) -> AdminPermissionAssignment:
    """撤销不物理删除，保留授权与撤销审计（§15.2）。"""
    row = (
        await db.execute(
            select(AdminPermissionAssignment).where(
                AdminPermissionAssignment.id == assignment_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("权限分配不存在")
    if row.revoked_at is not None:
        raise Conflict("该权限分配已撤销", code="ASSIGNMENT_ALREADY_REVOKED")

    row.revoked_at = _now()
    row.revoked_by = actor.id
    row.revoked_reason = reason
    await db.flush()

    await record_audit(
        db,
        action="assignment.permission_revoke",
        object_type="admin_permission_assignment",
        object_id=row.id,
        actor_id=actor.id,
        before={"revoked_at": None},
        after={"revoked_at": row.revoked_at.isoformat(), "reason": reason},
        reason=reason,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.ASSIGNMENT_CHANGED,
        subject_type="admin_permission_assignment",
        subject_id=row.id,
        payload={
            "user_id": str(row.user_id),
            "permission": row.permission,
            "action": "revoked",
        },
    )
    return row


# --------------------------------------------------------------------------- #
# 资料管理员资源分配（§3.3、§15.2）
# --------------------------------------------------------------------------- #
async def create_resource_assignment(
    db: AsyncSession,
    *,
    actor,
    payload: ResourceAssignmentCreate,
    request_id: str | None,
) -> ResourceAssignment:
    """指定资料管理员的作用范围；目标必须且只能有一个。"""
    if (payload.resource_id is None) == (payload.course_id is None):
        raise InvalidInput(
            "必须且只能指定 resource_id 或 course_id 之一",
            details={"reason": "exactly_one_target"},
        )
    from app.modules.identity.service import get_user

    target = await get_user(db, payload.user_id)
    from app.modules.identity.models import User
    # 与专业身份暂停/退出共用用户锁，串行处理同一账号的分配创建。
    await db.execute(select(User.id).where(User.id == target.id).with_for_update())

    if payload.resource_id is not None:
        from app.modules.content.models import Resource

        if await db.get(Resource, payload.resource_id) is None:
            raise NotFound("资源不存在")
    else:
        from app.modules.catalog.models import Course

        if await db.get(Course, payload.course_id) is None:
            raise NotFound("课程不存在")

    # 同 (user_id, 目标, capability) 唯一且覆盖已撤销记录，复用同一行重新授权。
    existing = (
        await db.execute(
            select(ResourceAssignment).where(
                ResourceAssignment.user_id == target.id,
                ResourceAssignment.resource_id == payload.resource_id,
                ResourceAssignment.course_id == payload.course_id,
                ResourceAssignment.capability == _as_str(payload.capability),
            )
        )
    ).scalar_one_or_none()

    before = None
    if existing is None:
        row = ResourceAssignment(
            user_id=target.id,
            resource_id=payload.resource_id,
            course_id=payload.course_id,
            capability=_as_str(payload.capability),
        )
        db.add(row)
    else:
        row = existing
        before = {"revoked_at": row.revoked_at.isoformat() if row.revoked_at else None}

    row.effective_at = payload.effective_at
    row.assigned_by = actor.id
    row.reason = payload.reason
    row.revoked_at = None
    row.revoked_reason = None
    await db.flush()

    await record_audit(
        db,
        action="assignment.resource_grant",
        object_type="resource_assignment",
        object_id=row.id,
        actor_id=actor.id,
        before=before,
        after={
            "user_id": str(row.user_id),
            "resource_id": str(row.resource_id) if row.resource_id else None,
            "course_id": str(row.course_id) if row.course_id else None,
            "capability": row.capability,
        },
        reason=payload.reason,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.ASSIGNMENT_CHANGED,
        subject_type="resource_assignment",
        subject_id=row.id,
        payload={
            "user_id": str(row.user_id),
            "resource_id": str(row.resource_id) if row.resource_id else None,
            "course_id": str(row.course_id) if row.course_id else None,
            "action": "granted",
        },
    )
    return row


async def list_resource_assignments(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> CursorPage[ResourceAssignmentOut]:
    size = normalize_page_size(limit)
    stmt = select(ResourceAssignment)
    if user_id is not None:
        stmt = stmt.where(ResourceAssignment.user_id == user_id)
    stmt = seek_descending(
        db, stmt, ResourceAssignment.created_at, ResourceAssignment.id, cursor
    )
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"ts": last.created_at.isoformat(), "id": str(last.id)})
    return CursorPage[ResourceAssignmentOut](
        items=[_resource_out(row) for row in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def revoke_resource_assignment(
    db: AsyncSession,
    *,
    actor,
    assignment_id: uuid.UUID,
    reason: str,
    request_id: str | None,
) -> ResourceAssignment:
    """撤销后立即停止新访问与新签名链接，不物理删除（§3.3）。"""
    row = (
        await db.execute(
            select(ResourceAssignment).where(ResourceAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("资源分配不存在")
    if row.revoked_at is not None:
        raise Conflict("该资源分配已撤销", code="ASSIGNMENT_ALREADY_REVOKED")

    row.revoked_at = _now()
    row.revoked_reason = reason
    await db.flush()

    await record_audit(
        db,
        action="assignment.resource_revoke",
        object_type="resource_assignment",
        object_id=row.id,
        actor_id=actor.id,
        before={"revoked_at": None},
        after={"revoked_at": row.revoked_at.isoformat(), "reason": reason},
        reason=reason,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.ASSIGNMENT_CHANGED,
        subject_type="resource_assignment",
        subject_id=row.id,
        payload={
            "user_id": str(row.user_id),
            "resource_id": str(row.resource_id) if row.resource_id else None,
            "course_id": str(row.course_id) if row.course_id else None,
            "action": "revoked",
        },
    )
    return row


# --------------------------------------------------------------------------- #
# 本人视角（§4.4）
# --------------------------------------------------------------------------- #
async def my_assignments(db: AsyncSession, *, user) -> MyAssignmentsOut:
    """本人当前有效的分配与权限；只查询本人，不泄露他人。"""
    now = _now()
    permissions = list(
        (
            await db.execute(
                select(AdminPermissionAssignment).where(
                    AdminPermissionAssignment.user_id == user.id,
                    AdminPermissionAssignment.scope == "global",
                    AdminPermissionAssignment.scope_catalog_id.is_(None),
                    AdminPermissionAssignment.scope_resource_id.is_(None),
                    AdminPermissionAssignment.revoked_at.is_(None),
                    or_(
                        AdminPermissionAssignment.valid_until.is_(None),
                        AdminPermissionAssignment.valid_until > now,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    resources = list(
        (
            await db.execute(
                select(ResourceAssignment).where(
                    ResourceAssignment.user_id == user.id,
                    ResourceAssignment.revoked_at.is_(None),
                    or_(
                        ResourceAssignment.effective_at.is_(None),
                        ResourceAssignment.effective_at <= now,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return MyAssignmentsOut(
        permissions=[_permission_out(row) for row in permissions],
        resource_assignments=[_resource_out(row) for row in resources],
    )


# --------------------------------------------------------------------------- #
# 资源级鉴权（§3.3；供其他模块复用）
# --------------------------------------------------------------------------- #
async def active_assignment_resource_ids(
    db: AsyncSession, user, capability: AssignmentCapability | str | None = None
) -> set[uuid.UUID]:
    """本人当前有效的资源级分配（未撤销且已生效）。"""
    if user is None:
        return set()
    now = _now()
    stmt = select(ResourceAssignment.resource_id).where(
        ResourceAssignment.user_id == user.id,
        ResourceAssignment.resource_id.is_not(None),
        ResourceAssignment.revoked_at.is_(None),
        or_(
            ResourceAssignment.effective_at.is_(None),
            ResourceAssignment.effective_at <= now,
        ),
    )
    if capability is not None:
        stmt = stmt.where(ResourceAssignment.capability == _as_str(capability))
    return {row for row in (await db.execute(stmt)).scalars().all() if row is not None}


async def active_assignment_course_ids(
    db: AsyncSession, user, capability: AssignmentCapability | str | None = None
) -> set[uuid.UUID]:
    """本人当前有效的课程级分配（未撤销且已生效）。"""
    if user is None:
        return set()
    now = _now()
    stmt = select(ResourceAssignment.course_id).where(
        ResourceAssignment.user_id == user.id,
        ResourceAssignment.course_id.is_not(None),
        ResourceAssignment.revoked_at.is_(None),
        or_(
            ResourceAssignment.effective_at.is_(None),
            ResourceAssignment.effective_at <= now,
        ),
    )
    if capability is not None:
        stmt = stmt.where(ResourceAssignment.capability == _as_str(capability))
    return {row for row in (await db.execute(stmt)).scalars().all() if row is not None}


async def can_manage_resource(
    db: AsyncSession,
    user,
    resource_id: uuid.UUID,
    capability: AssignmentCapability | str,
) -> bool:
    """owner（内容管理关系）或该资源／其所属课程上存在有效分配（§3.3）。

    只回答「可对哪一个对象做」；审核等动作仍由对应权限（如 `RESOURCE_REVIEW`）决定，
    本函数不放宽既有审核语义。
    """
    if user is None:
        return False
    from app.modules.content.models import Resource

    resource = await db.get(Resource, resource_id)
    if resource is None:
        return False
    if resource.owner_id == user.id:
        return True
    now = _now()
    found = await db.scalar(
        select(ResourceAssignment.id)
        .where(
            ResourceAssignment.user_id == user.id,
            ResourceAssignment.capability == _as_str(capability),
            ResourceAssignment.revoked_at.is_(None),
            or_(
                ResourceAssignment.effective_at.is_(None),
                ResourceAssignment.effective_at <= now,
            ),
            or_(
                ResourceAssignment.resource_id == resource_id,
                ResourceAssignment.course_id == resource.course_id,
            ),
        )
        .limit(1)
    )
    return found is not None
