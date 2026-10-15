"""content 路由（设计文档 §16、§25.2）。

只做解析与调用；业务规则全部在 service.py。前缀由 main.py 统一加 `/api/v1`。
影响关键状态的 POST 支持 `Idempotency-Key`（§4、§25.1）。
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Request, status

from app.core.deps import get_current_user, get_db, get_optional_user, get_request_id
from app.core.enums import ResourceType
from app.core.idempotency import lookup, normalize_key, remember
from app.core.permissions import RESOURCE_REVISE, RESOURCE_SUBMIT, require
from app.modules.content import service
from app.modules.content.schemas import (
    PublicationOut,
    ResourceCreate,
    ResourceCreatedOut,
    ResourceDetailOut,
    ResourceListOut,
    SubmitOut,
    UploadCreateIn,
    UploadCreateOut,
    UploadStatusOut,
    VersionCreate,
    VersionFilesOut,
    VersionOut,
    VersionUpdate,
    WithdrawIn,
)

router = APIRouter(tags=["content"])


async def _idempotent(
    db,
    *,
    scope: str,
    request: Request,
    payload: Any,
    actor_id: uuid.UUID | None,
    action: Callable[[], Awaitable[dict]],
) -> dict:
    """相同 Idempotency-Key 返回同一业务结果；摘要不一致返回冲突（§25.1）。"""
    key = request.headers.get("Idempotency-Key")
    if key:
        key = normalize_key(key)
        cached = await lookup(db, scope=scope, key=key, payload=payload, actor_id=actor_id)
        if cached is not None:
            return cached
    result = await action()
    if key:
        await remember(
            db,
            scope=scope,
            key=key,
            payload=payload,
            actor_id=actor_id,
            response_status=200,
            response=result,
        )
    return result


# --------------------------------------------------------------------------- #
# 资源
# --------------------------------------------------------------------------- #
@router.post("/resources", response_model=ResourceCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_resource(
    payload: ResourceCreate,
    request: Request,
    user=Depends(require(RESOURCE_SUBMIT)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    body = payload.model_dump(mode="json")

    async def action() -> dict:
        result = await service.create_resource(db, user=user, payload=payload, request_id=request_id)
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope="content.create_resource",
        request=request,
        payload={"body": body},
        actor_id=user.id,
        action=action,
    )


@router.get("/resources", response_model=ResourceListOut)
async def list_resources(
    course_id: uuid.UUID | None = None,
    college_id: uuid.UUID | None = None,
    offering_id: uuid.UUID | None = None,
    semester_id: uuid.UUID | None = None,
    year: int | None = None,
    resource_type: ResourceType | None = None,
    keyword: str | None = None,
    sort: str = "latest",
    limit: int | None = None,
    cursor: str | None = None,
    user=Depends(get_optional_user),
    db=Depends(get_db),
):
    return await service.list_resources(
        db,
        course_id=course_id,
        college_id=college_id,
        offering_id=offering_id,
        semester_id=semester_id,
        year=year,
        resource_type=resource_type,
        keyword=keyword,
        sort=sort,
        limit=limit,
        cursor=cursor,
    )


@router.get("/resources/{resource_id}", response_model=ResourceDetailOut)
async def get_resource(
    resource_id: uuid.UUID,
    user=Depends(get_optional_user),
    db=Depends(get_db),
):
    return await service.get_resource_detail(db, user=user, resource_id=resource_id)


@router.post(
    "/resources/{resource_id}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    resource_id: uuid.UUID,
    payload: VersionCreate,
    request: Request,
    user=Depends(require(RESOURCE_REVISE)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    body = payload.model_dump(mode="json")

    async def action() -> dict:
        result = await service.create_version(
            db, user=user, resource_id=resource_id, payload=payload, request_id=request_id
        )
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope=f"content.create_version:{resource_id}",
        request=request,
        payload={"body": body},
        actor_id=user.id,
        action=action,
    )


@router.patch("/resources/{resource_id}/versions/{version_id}", response_model=VersionOut)
async def update_version(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: VersionUpdate,
    user=Depends(require(RESOURCE_REVISE)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    return await service.update_version(
        db,
        user=user,
        resource_id=resource_id,
        version_id=version_id,
        payload=payload,
        request_id=request_id,
    )


@router.post(
    "/resources/{resource_id}/versions/{version_id}/submit",
    response_model=SubmitOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_version(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    user=Depends(require(RESOURCE_SUBMIT)),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    async def action() -> dict:
        result = await service.submit_version(
            db,
            user=user,
            resource_id=resource_id,
            version_id=version_id,
            request_id=request_id,
        )
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope=f"content.submit_version:{version_id}",
        request=request,
        payload={"resource_id": str(resource_id), "version_id": str(version_id)},
        actor_id=user.id,
        action=action,
    )


@router.post("/resources/{resource_id}/withdraw", response_model=PublicationOut)
async def withdraw_resource(
    resource_id: uuid.UUID,
    payload: WithdrawIn,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    body = payload.model_dump(mode="json")

    async def action() -> dict:
        result = await service.withdraw_resource(
            db, user=user, resource_id=resource_id, payload=payload, request_id=request_id
        )
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope=f"content.withdraw:{resource_id}",
        request=request,
        payload={"body": body},
        actor_id=user.id,
        action=action,
    )


@router.get(
    "/resources/{resource_id}/versions/{version_id}/files",
    response_model=VersionFilesOut,
)
async def list_version_files(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    user=Depends(get_optional_user),
    db=Depends(get_db),
):
    return await service.list_version_files(
        db, user=user, resource_id=resource_id, version_id=version_id
    )


# --------------------------------------------------------------------------- #
# 上传会话
# --------------------------------------------------------------------------- #
@router.post("/uploads", response_model=UploadCreateOut, status_code=status.HTTP_201_CREATED)
async def create_upload(
    payload: UploadCreateIn,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    body = payload.model_dump(mode="json")

    async def action() -> dict:
        result = await service.create_upload_session(db, user=user, payload=payload)
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope="content.create_upload",
        request=request,
        payload={"body": body},
        actor_id=user.id,
        action=action,
    )


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=UploadStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_upload(
    upload_id: uuid.UUID,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db),
    request_id: str = Depends(get_request_id),
):
    async def action() -> dict:
        result = await service.complete_upload(
            db, user=user, upload_id=upload_id, request_id=request_id
        )
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope=f"content.complete_upload:{upload_id}",
        request=request,
        payload={"upload_id": str(upload_id)},
        actor_id=user.id,
        action=action,
    )


@router.get("/uploads/{upload_id}", response_model=UploadStatusOut)
async def get_upload(
    upload_id: uuid.UUID,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    return await service.get_upload_session(db, user=user, upload_id=upload_id)
