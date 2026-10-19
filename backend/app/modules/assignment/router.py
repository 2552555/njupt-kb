"""assignment 路由（用户权责体系 §3、§15.2、§4.4）。

只做参数解析与权限依赖；业务规则在 service.py。前缀由 main.py 统一加 `/api/v1`。
管理接口需 `ASSIGNMENT_MANAGE`（角色权限或未过期的按需授权均可满足）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.pagination import CursorPage
from app.core.permissions import ASSIGNMENT_MANAGE, require
from app.modules.assignment import service
from app.modules.assignment.schemas import (
    MyAssignmentsOut,
    PermissionAssignmentCreate,
    PermissionAssignmentOut,
    PermissionAssignmentRevokeIn,
    ResourceAssignmentCreate,
    ResourceAssignmentOut,
    ResourceAssignmentRevokeIn,
)

router = APIRouter(tags=["assignment"])


@router.post(
    "/admin/permission-assignments",
    response_model=PermissionAssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def grant_permission_assignment(
    payload: PermissionAssignmentCreate,
    user=Depends(require(ASSIGNMENT_MANAGE)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    return await service.create_permission_assignment(
        db, actor=user, payload=payload, request_id=request_id
    )


@router.get("/admin/permission-assignments", response_model=CursorPage[PermissionAssignmentOut])
async def list_permission_assignments(
    user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    user=Depends(require(ASSIGNMENT_MANAGE)),
    db=Depends(get_db),
):
    return await service.list_permission_assignments(
        db, user_id=user_id, cursor=cursor, limit=limit
    )


@router.post(
    "/admin/permission-assignments/{assignment_id}/revoke",
    response_model=PermissionAssignmentOut,
)
async def revoke_permission_assignment(
    assignment_id: uuid.UUID,
    payload: PermissionAssignmentRevokeIn,
    user=Depends(require(ASSIGNMENT_MANAGE)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    return await service.revoke_permission_assignment(
        db, actor=user, assignment_id=assignment_id, reason=payload.reason, request_id=request_id
    )


@router.post(
    "/admin/resource-assignments",
    response_model=ResourceAssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def grant_resource_assignment(
    payload: ResourceAssignmentCreate,
    user=Depends(require(ASSIGNMENT_MANAGE)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    return await service.create_resource_assignment(
        db, actor=user, payload=payload, request_id=request_id
    )


@router.get("/admin/resource-assignments", response_model=CursorPage[ResourceAssignmentOut])
async def list_resource_assignments(
    user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    user=Depends(require(ASSIGNMENT_MANAGE)),
    db=Depends(get_db),
):
    return await service.list_resource_assignments(
        db, user_id=user_id, cursor=cursor, limit=limit
    )


@router.post(
    "/admin/resource-assignments/{assignment_id}/revoke",
    response_model=ResourceAssignmentOut,
)
async def revoke_resource_assignment(
    assignment_id: uuid.UUID,
    payload: ResourceAssignmentRevokeIn,
    user=Depends(require(ASSIGNMENT_MANAGE)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    return await service.revoke_resource_assignment(
        db, actor=user, assignment_id=assignment_id, reason=payload.reason, request_id=request_id
    )


@router.get("/me/assignments", response_model=MyAssignmentsOut)
async def my_assignments(user=Depends(get_current_user), db=Depends(get_db)):
    return await service.my_assignments(db, user=user)
