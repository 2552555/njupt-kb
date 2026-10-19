"""settlement 路由（用户权责体系 §16.2、§16.3）。

只做参数解析、权限依赖与调用 `service`；业务规则不在这里。
前缀由 `app/main.py` 统一加 `/api/v1`，本模块不自行决定前缀（docs/ARCHITECTURE.md §1）。

开关关闭（`COMMERCE_ENABLED=false` 或 `COMMERCE_COMPLIANCE_GATE_CLOSED=true`）时：
- 写接口（确认、异议、生成、批准、付款、核对、账务更正）由 `service` 抛 `NotEnabled`
  → HTTP 503 / `FEATURE_NOT_ENABLED`；
- 只读接口（`/me/settlement-statements`、后台查询）不涉及资金，仍正常工作。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.pagination import CursorPage
from app.core.permissions import PAYOUT_MANAGE, SETTLEMENT_MANAGE, require
from app.modules.settlement import service
from app.modules.settlement.schemas import (
    LedgerCorrectionIn,
    LedgerCorrectionOut,
    MySettlementStatementOut,
    PayoutAttemptCreateIn,
    PayoutAttemptOut,
    PayoutOut,
    PayoutReconcileIn,
    SettlementStatementOut,
    StatementApproveIn,
    StatementConfirmIn,
    StatementDisputeIn,
    StatementGenerateIn,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["settlement"])


# --------------------------------------------------------------------------- #
# 用户/贡献者侧（登录即可）
# --------------------------------------------------------------------------- #
@router.get("/me/settlement-statements", response_model=CursorPage[MySettlementStatementOut])
async def list_my_settlement_statements(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[MySettlementStatementOut]:
    """本人结算单与付款状态（只读，游标分页，账户只显示尾号）。"""
    return await service.list_my_statements(db, user=user, limit=limit, cursor=cursor)


@router.post("/settlement-statements/{statement_id}/confirm", response_model=SettlementStatementOut)
async def confirm_settlement_statement(
    statement_id: uuid.UUID,
    payload: StatementConfirmIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> SettlementStatementOut:
    """本人合同收款主体确认明细（版本校验）。"""
    return await service.confirm_statement(
        db, user=user, statement_id=statement_id, payload=payload, request_id=request_id
    )


@router.post(
    "/settlement-statements/{statement_id}/disputes",
    response_model=SettlementStatementOut,
    status_code=status.HTTP_201_CREATED,
)
async def dispute_settlement_statement(
    statement_id: uuid.UUID,
    payload: StatementDisputeIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> SettlementStatementOut:
    """提出结算异议：关联争议行与金额，置 disputed（先回调查）。"""
    return await service.dispute_statement(
        db, user=user, statement_id=statement_id, payload=payload, request_id=request_id
    )


# --------------------------------------------------------------------------- #
# 后台侧：结算单（§16.3）
# --------------------------------------------------------------------------- #
@router.get("/admin/settlement-statements", response_model=CursorPage[SettlementStatementOut])
async def list_admin_settlement_statements(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    actor: "User" = Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[SettlementStatementOut]:
    """结算单列表（只读，分页）。"""
    return await service.list_admin_statements(db, limit=limit, cursor=cursor)


@router.post(
    "/admin/settlement-statements",
    response_model=SettlementStatementOut,
    status_code=status.HTTP_201_CREATED,
)
async def generate_settlement_statement(
    payload: StatementGenerateIn,
    actor: "User" = Depends(require(SETTLEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> SettlementStatementOut:
    """生成结算草稿：按成熟和预留过滤，只纳入可结算净额；比例/最低额未配置时抛 NotEnabled。"""
    return await service.generate_statement(db, actor=actor, payload=payload, request_id=request_id)


@router.post(
    "/admin/settlement-statements/{statement_id}/approve",
    response_model=SettlementStatementOut,
)
async def approve_settlement_statement(
    statement_id: uuid.UUID,
    payload: StatementApproveIn,
    actor: "User" = Depends(require(SETTLEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> SettlementStatementOut:
    """批准并锁批：财务权限与版本校验；锁入批次后金额不可再被其它批次使用。"""
    return await service.approve_statement(
        db, actor=actor, statement_id=statement_id, payload=payload, request_id=request_id
    )


# --------------------------------------------------------------------------- #
# 后台侧：付款与核对（§16.3）
# --------------------------------------------------------------------------- #
@router.get("/admin/payouts", response_model=CursorPage[PayoutOut])
async def list_admin_payouts(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    actor: "User" = Depends(require(PAYOUT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[PayoutOut]:
    """付款计划列表（只读，分页）。"""
    return await service.list_admin_payouts(db, limit=limit, cursor=cursor)


@router.post(
    "/admin/payouts/{payout_id}/attempts",
    response_model=PayoutAttemptOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_payout_attempt(
    payout_id: uuid.UUID,
    payload: PayoutAttemptCreateIn,
    actor: "User" = Depends(require(PAYOUT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> PayoutAttemptOut:
    """预留对外付款操作：准入和账户快照核验；最低付款额未配置时抛 NotEnabled。"""
    return await service.create_payout_attempt(
        db, actor=actor, payout_id=payout_id, payload=payload, request_id=request_id
    )


@router.get("/admin/payouts/{payout_id}/attempts", response_model=CursorPage[PayoutAttemptOut])
async def list_payout_attempts(
    payout_id: uuid.UUID, limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = None, actor: "User" = Depends(require(PAYOUT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[PayoutAttemptOut]:
    return await service.list_payout_attempts(db, payout_id=payout_id, limit=limit, cursor=cursor)


@router.post("/admin/payout-attempts/{attempt_id}/reconcile", response_model=PayoutAttemptOut)
async def reconcile_payout_attempt(
    attempt_id: uuid.UUID,
    payload: PayoutReconcileIn,
    actor: "User" = Depends(require(PAYOUT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> PayoutAttemptOut:
    """核对 succeeded/failed/uncertain：真实证据必填；uncertain 持续占款不释放。"""
    return await service.reconcile_payout_attempt(
        db, actor=actor, attempt_id=attempt_id, payload=payload, request_id=request_id
    )


# --------------------------------------------------------------------------- #
# 后台侧：账务更正（§16.3）
# --------------------------------------------------------------------------- #
@router.post(
    "/admin/ledger-corrections",
    response_model=LedgerCorrectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_ledger_correction(
    payload: LedgerCorrectionIn,
    actor: "User" = Depends(require(SETTLEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> LedgerCorrectionOut:
    """提出及确认账务更正：关联原记录，不提供直接改余额。"""
    return await service.record_ledger_correction(
        db, actor=actor, payload=payload, request_id=request_id
    )
