"""creator 路由：只做参数解析与调用，业务规则在 service.py。

前缀由 app/main.py 统一加 /api/v1，模块不自行决定前缀（docs/ARCHITECTURE.md §1）。
只读接口（贡献者资料、我的贡献、我的账本）不涉及资金，任何阶段都可用；
写接口（提现、结算）在 `COMMERCE_ENABLED=false` 或规则未配置时抛 NotEnabled（§25.2）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.pagination import CursorPage
from app.core.permissions import SETTLEMENT_MANAGE, require
from app.modules.creator import service
from app.modules.creator.schemas import (
    ContributionOut,
    CreatorLedgerEntryOut,
    CreatorProfileOut,
    CreatorWithdrawalOut,
    SettlementRunRequest,
    SettlementRunResult,
    WithdrawalRequest,
)

router = APIRouter(tags=["creator"])


@router.get("/creators/{user_id}", response_model=CreatorProfileOut)
async def get_creator_profile(
    user_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CreatorProfileOut:
    """贡献者公开资料（只读）。"""
    return await service.get_creator_profile(db, user_id)


@router.get("/me/contributions", response_model=CursorPage[ContributionOut])
async def list_my_contributions(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ContributionOut]:
    """我的贡献记录（只读，游标分页）。"""
    return await service.list_contributions(db, user_id=user.id, limit=limit, cursor=cursor)


@router.get("/me/ledger", response_model=CursorPage[CreatorLedgerEntryOut])
async def list_my_ledger(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CreatorLedgerEntryOut]:
    """我的账本分录（只读）。"""
    return await service.list_ledger(db, user_id=user.id, limit=limit, cursor=cursor)


@router.post(
    "/me/withdrawals", response_model=CreatorWithdrawalOut, status_code=status.HTTP_201_CREATED
)
async def create_my_withdrawal(
    body: WithdrawalRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CreatorWithdrawalOut:
    return await service.request_withdrawal(
        db,
        user=user,
        payload=body,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.get("/me/withdrawals", response_model=CursorPage[CreatorWithdrawalOut])
async def list_my_withdrawals(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CreatorWithdrawalOut]:
    """我的提现记录（只读）。"""
    return await service.list_withdrawals(db, user_id=user.id, limit=limit, cursor=cursor)


@router.post("/admin/settlements/run", response_model=SettlementRunResult)
async def run_settlement(
    body: SettlementRunRequest,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SettlementRunResult:
    """按已批准规则计算某周期结算（需 settlement.manage）；规则未配置时抛 NotEnabled。"""
    return await service.run_settlement(
        db, actor=actor, payload=body, request_id=request_id
    )
