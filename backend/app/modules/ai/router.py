"""ai 路由（设计文档 §23、§25.2）。

只做解析与调用；业务规则全部在 service.py。前缀由 main.py 统一加 `/api/v1`。
`settings.AI_ENABLED=false` 时会调用模型的接口在 service 层抛 `NotEnabled`（503），
此处不返回假成功。

影响额度的 POST 支持 `Idempotency-Key`（§25.1）。
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Request, status

from app.core.deps import get_current_user, get_db, get_optional_user
from app.core.idempotency import lookup, normalize_key, remember
from app.modules.ai import service
from app.modules.ai.schemas import (
    AIProcessCreateIn,
    AIProcessOut,
    BudgetOut,
    QAIn,
    QAOut,
    VersionAIOut,
)

router = APIRouter(tags=["ai"])


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
            response_status=202,
            response=result,
        )
    return result


@router.post("/ai/processes", response_model=AIProcessOut, status_code=status.HTTP_202_ACCEPTED)
async def create_ai_process(
    payload: AIProcessCreateIn,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """请求某资源版本的某类处理；AI 未启用时返回 503 FEATURE_NOT_ENABLED。"""
    body = payload.model_dump(mode="json")

    async def action() -> dict:
        process = await service.request_process(
            db,
            resource_id=payload.resource_id,
            version_id=payload.version_id,
            kind=payload.kind,
            requested_by=user.id,
            allow_external=payload.allow_external,
        )
        return AIProcessOut.model_validate(process).model_dump(mode="json")

    return await _idempotent(
        db,
        scope=f"ai.request_process:{payload.version_id}",
        request=request,
        payload={"body": body},
        actor_id=user.id,
        action=action,
    )


@router.get("/ai/processes/{process_id}", response_model=AIProcessOut)
async def get_ai_process(
    process_id: uuid.UUID,
    user=Depends(get_optional_user),
    db=Depends(get_db),
):
    """查询处理状态与结果（同样要求对来源版本有完整读取权限）。"""
    return await service.get_process(db, user=user, process_id=process_id)


@router.get("/ai/budget", response_model=BudgetOut)
async def get_ai_budget(
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """当前用户与全局的预算使用情况（只读，不创建记录、不扣额度）。"""
    return await service.budget_snapshot(db, user=user)


@router.post("/ai/qa", response_model=QAOut)
async def ask_course_question(
    payload: QAIn,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """课程问答（P4）；只引用用户有权限访问的资源版本。"""
    body = payload.model_dump(mode="json")

    async def action() -> dict:
        result = await service.qa(db, user=user, payload=payload)
        return result.model_dump(mode="json")

    return await _idempotent(
        db,
        scope="ai.qa",
        request=request,
        payload={"body": body},
        actor_id=user.id,
        action=action,
    )


@router.get("/resources/{resource_id}/versions/{version_id}/ai", response_model=VersionAIOut)
async def list_version_ai(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    user=Depends(get_optional_user),
    db=Depends(get_db),
):
    """该版本已有的生成结果清单，带来源标记（§23）。"""
    return await service.list_version_ai(
        db, user=user, resource_id=resource_id, version_id=version_id
    )
