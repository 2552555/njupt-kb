"""access 模块路由：只做解析与调用，业务规则在 service.py。

前缀由 main.py 统一加 /api/v1，模块不自行决定前缀（docs/ARCHITECTURE.md §1）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    get_current_user,
    get_db,
    get_optional_user,
    get_request_id,
    parse_uuid,
)
from app.core.pagination import CursorPage
from app.core.permissions import CATALOG_MANAGE, ENTITLEMENT_GRANT, require
from app.modules.access import service
from app.modules.access.schemas import (
    AccessPolicyOut,
    AccessPolicyUpdate,
    DownloadGrantRequest,
    DownloadGrantResult,
    EntitlementCreate,
    EntitlementOut,
    EntitlementRevoke,
    PreviewGrantRequest,
    PreviewGrantResult,
)

router = APIRouter(tags=["access"])


@router.post(
    "/resources/{resource_id}/preview-grants",
    response_model=PreviewGrantResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_preview_grant(
    resource_id: str,
    body: PreviewGrantRequest,
    user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> PreviewGrantResult:
    rid = parse_uuid(resource_id, "resource_id")
    return await service.issue_preview_grant(
        db, user=user, resource_id=rid, version_id=body.version_id, request_id=request_id
    )


@router.post(
    "/resources/{resource_id}/download-grants",
    response_model=DownloadGrantResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_download_grant(
    resource_id: str,
    body: DownloadGrantRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> DownloadGrantResult:
    rid = parse_uuid(resource_id, "resource_id")
    return await service.issue_download_grant(
        db,
        user=user,
        resource_id=rid,
        version_id=body.version_id,
        file_id=body.file_id,
        idempotency_key=idempotency_key or "",
        request_id=request_id,
    )


@router.get("/me/entitlements", response_model=CursorPage[EntitlementOut])
async def list_my_entitlements(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
) -> CursorPage[EntitlementOut]:
    return await service.list_entitlements(db, user_id=user.id, limit=limit, cursor=cursor)


@router.get("/admin/entitlements", response_model=CursorPage[EntitlementOut])
async def admin_list_entitlements(
    user=Depends(require(ENTITLEMENT_GRANT)),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
) -> CursorPage[EntitlementOut]:
    target_id = parse_uuid(user_id, "user_id") if user_id else None
    return await service.list_entitlements(db, user_id=target_id, limit=limit, cursor=cursor)


@router.post(
    "/admin/entitlements",
    response_model=EntitlementOut,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_entitlement(
    body: EntitlementCreate,
    actor=Depends(require(ENTITLEMENT_GRANT)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> EntitlementOut:
    entitlement = await service.create_entitlement(
        db, actor=actor, payload=body, request_id=request_id
    )
    return EntitlementOut.model_validate(entitlement)


@router.post("/admin/entitlements/{entitlement_id}/revoke", response_model=EntitlementOut)
async def admin_revoke_entitlement(
    entitlement_id: str,
    body: EntitlementRevoke,
    actor=Depends(require(ENTITLEMENT_GRANT)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> EntitlementOut:
    eid = parse_uuid(entitlement_id, "entitlement_id")
    entitlement = await service.revoke_entitlement(
        db, actor=actor, entitlement_id=eid, reason=body.reason, request_id=request_id
    )
    return EntitlementOut.model_validate(entitlement)


@router.put("/admin/resources/{resource_id}/access-policy", response_model=AccessPolicyOut)
async def admin_set_access_policy(
    resource_id: str,
    body: AccessPolicyUpdate,
    actor=Depends(require(CATALOG_MANAGE)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> AccessPolicyOut:
    rid = parse_uuid(resource_id, "resource_id")
    policy = await service.set_access_policy(
        db, actor=actor, resource_id=rid, payload=body, request_id=request_id
    )
    return AccessPolicyOut.model_validate(policy)
