"""cases 路由（设计文档 §16.2、§16.3）。

只做参数解析、权限依赖与调用 `service`；业务规则不在这里。
前缀由 `app/main.py` 统一加 `/api/v1`，本模块不自行决定前缀。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.enums import AccountStatus, CaseStatus, CaseType
from app.core.errors import Forbidden
from app.core.pagination import CursorPage
from app.core.permissions import (
    CASE_HANDLE,
    RIGHTS_NOTICE_HANDLE,
    effective_permissions,
    require,
)
from app.modules.cases import service
from app.modules.cases.schemas import (
    CaseAdminOut,
    CaseCreateIn,
    CaseDetailOut,
    CaseEventIn,
    CaseEventOut,
    CaseListItemOut,
    CounterStatementIn,
    CounterStatementOut,
    MeasureCreateIn,
    MeasureOut,
    RightsNoticeCreateIn,
    RightsNoticeReceiptOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["cases"])


async def _case_or_rights_handler(
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> "User":
    """案件队列/事件：CASE_HANDLE 或 RIGHTS_NOTICE_HANDLE 任一即可（§16.3）。"""
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能执行此操作")
    granted = await effective_permissions(db, user)
    if not ({CASE_HANDLE, RIGHTS_NOTICE_HANDLE} & granted):
        raise Forbidden("没有处理案件的权限")
    return user


# --------------------------------------------------------------------------- #
# 用户侧：案件
# --------------------------------------------------------------------------- #


@router.post("/cases", response_model=CaseDetailOut, response_model_exclude_none=True,
             status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: CaseCreateIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CaseDetailOut:
    case = await service.create_case(db, applicant=user, payload=payload, request_id=request_id)
    # 创建者是申请人，按本人视角返回（不含内部字段）。
    return await service.get_my_case(db, user=user, case_id=case.id)


@router.get("/me/cases", response_model=CursorPage[CaseListItemOut])
async def list_my_cases(
    case_type: list[CaseType] | None = Query(default=None),
    state: list[CaseStatus] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CaseListItemOut]:
    return await service.list_my_cases(
        db, user=user, case_types=case_type, states=state, limit=limit, cursor=cursor
    )


@router.get("/me/cases/{case_id}", response_model=CaseDetailOut, response_model_exclude_none=True)
async def get_my_case(
    case_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CaseDetailOut:
    return await service.get_my_case(db, user=user, case_id=case_id)


# --------------------------------------------------------------------------- #
# 用户侧：侵权通知与反通知（无需购买即可访问）
# --------------------------------------------------------------------------- #


@router.post("/rights-notices", response_model=RightsNoticeReceiptOut,
             status_code=status.HTTP_201_CREATED)
async def create_rights_notice(
    payload: RightsNoticeCreateIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> RightsNoticeReceiptOut:
    return await service.create_rights_notice(
        db, claimant=user, payload=payload, request_id=request_id
    )


@router.post("/rights-notices/{notice_id}/counter-statements",
             response_model=CounterStatementOut, status_code=status.HTTP_201_CREATED)
async def create_counter_statement(
    notice_id: uuid.UUID,
    payload: CounterStatementIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CounterStatementOut:
    statement = await service.create_counter_statement(
        db, user=user, notice_id=notice_id, payload=payload, request_id=request_id
    )
    return service.counter_statement_out(statement)


# --------------------------------------------------------------------------- #
# 后台侧：案件队列与事件流转
# --------------------------------------------------------------------------- #


@router.get("/admin/cases", response_model=CursorPage[CaseAdminOut])
async def list_admin_cases(
    case_type: list[CaseType] | None = Query(default=None),
    state: list[CaseStatus] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(_case_or_rights_handler),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CaseAdminOut]:
    return await service.list_admin_cases(
        db, case_types=case_type, states=state, limit=limit, cursor=cursor
    )


@router.post("/admin/cases/{case_id}/events", response_model=CaseEventOut,
             status_code=status.HTTP_201_CREATED)
async def append_case_event(
    case_id: uuid.UUID,
    payload: CaseEventIn,
    user: "User" = Depends(_case_or_rights_handler),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CaseEventOut:
    event = await service.append_case_event(
        db, case_id=case_id, actor=user, payload=payload, request_id=request_id
    )
    return CaseEventOut.model_validate(event)


# --------------------------------------------------------------------------- #
# 后台侧：必要措施
# --------------------------------------------------------------------------- #


@router.post("/admin/cases/{case_id}/measures", response_model=MeasureOut,
             status_code=status.HTTP_201_CREATED)
async def take_measure(
    case_id: uuid.UUID,
    payload: MeasureCreateIn,
    user: "User" = Depends(require(CASE_HANDLE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> MeasureOut:
    measure, applied = await service.take_measure(
        db, case_id=case_id, actor=user, payload=payload, request_id=request_id
    )
    return service.measure_out(measure, applied=applied)


@router.post("/admin/cases/{case_id}/measures/{measure_id}/release", response_model=MeasureOut)
async def release_measure(
    case_id: uuid.UUID,
    measure_id: uuid.UUID,
    user: "User" = Depends(require(CASE_HANDLE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> MeasureOut:
    measure = await service.release_measure(
        db, case_id=case_id, measure_id=measure_id, actor=user, request_id=request_id
    )
    return service.measure_out(measure)
