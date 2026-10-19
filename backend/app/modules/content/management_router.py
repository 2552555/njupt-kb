"""Authenticated workspace routes. Raw files still require access grants."""

import uuid

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user, get_db
from app.core.pagination import CursorPage
from app.modules.content import management
from app.modules.content.schemas import ManagedResourceOut, VersionDetailOut, VersionOut

router = APIRouter(tags=["content-workspace"])


@router.get("/me/resources", response_model=CursorPage[ManagedResourceOut])
async def my_resources(
    cursor: str | None = None,
    limit: int | None = None,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    return await management.list_my_resources(db, user=user, cursor=cursor, limit=limit)


@router.get("/resources/{resource_id}/versions", response_model=list[VersionOut])
async def versions(
    resource_id: uuid.UUID, user=Depends(get_current_user), db=Depends(get_db)
):
    return await management.list_versions(db, user=user, resource_id=resource_id)


@router.get(
    "/resources/{resource_id}/versions/{version_id}", response_model=VersionDetailOut
)
async def version_detail(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    return await management.get_version_detail(
        db, user=user, resource_id=resource_id, version_id=version_id
    )


@router.post(
    "/resources/{resource_id}/versions/{version_id}/files/{file_id}/inspection-grants"
)
async def inspect_file(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    file_id: uuid.UUID,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    from app.modules.access.service import issue_inspection_grant

    return await issue_inspection_grant(
        db, user=user, resource_id=resource_id, version_id=version_id, file_id=file_id
    )
