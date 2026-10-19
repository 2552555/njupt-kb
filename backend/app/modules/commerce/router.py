"""commerce 路由：只做参数解析与调用，业务规则在 service.py。

前缀由 app/main.py 统一加 /api/v1，模块不自行决定前缀（docs/ARCHITECTURE.md §1）。

`settings.COMMERCE_ENABLED=false` 时：
- 写接口（下单、取消、回调、退款、确认退款）抛 `NotEnabled` → HTTP 503 / `FEATURE_NOT_ENABLED`；
- 只读接口（产品、订单详情、退款列表、对账）不涉及资金，仍正常工作。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.pagination import CursorPage
from app.core.permissions import (
    FINANCE_VIEW,
    LEDGER_VIEW,
    SETTLEMENT_MANAGE,
    require,
)
from app.modules.commerce import channels, service
from app.modules.commerce.schemas import (
    AccrualResult,
    ComponentAllocationOut,
    OrderCreate,
    OrderOut,
    PaymentAttemptOut,
    ProductOut,
    ReconciliationItem,
    RefundConfirmIn,
    RefundCreate,
    RefundOut,
    ReversalResult,
    ReservationCreate,
    ReservationOut,
    SettleableBalanceOut,
)

router = APIRouter(tags=["commerce"])


@router.get("/products", response_model=CursorPage[ProductOut])
async def list_products(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ProductOut]:
    """已上架产品与价格（只读，不涉及资金）。"""
    return await service.list_products(db, limit=limit, cursor=cursor)


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> OrderOut:
    return await service.create_order(
        db,
        user=user,
        payload=body,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """订单详情（只读）。"""
    return await service.get_order(db, user=user, order_id=order_id)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    reason: str | None = Query(default=None),
) -> OrderOut:
    return await service.cancel_order(
        db, user=user, order_id=order_id, reason=reason, request_id=request_id
    )


@router.post("/payments/{channel}/notify", response_model=PaymentAttemptOut)
async def payment_notify(
    channel: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PaymentAttemptOut:
    """渠道回调：必须验签；未配置渠道时返回 503 而不是 200（§24.3、§25.2）。"""
    raw_body = await request.body()
    adapter = channels.get_channel(channel)
    event = adapter.verify_callback(headers=request.headers, raw_body=raw_body)
    attempt = await service.confirm_payment(db, channel=channel, payload=event.to_payload())
    return PaymentAttemptOut.model_validate(attempt)


@router.post("/refunds", response_model=RefundOut, status_code=status.HTTP_201_CREATED)
async def create_refund(
    body: RefundCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RefundOut:
    return await service.create_refund(
        db,
        user=user,
        payload=body,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.get("/me/refunds", response_model=CursorPage[RefundOut])
async def list_my_refunds(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[RefundOut]:
    """我的退款记录（只读）。"""
    return await service.list_refunds(
        db, user_id=user.id, limit=limit, cursor=cursor
    )


@router.get("/admin/refunds", response_model=CursorPage[RefundOut])
async def admin_list_refunds(
    user_id: uuid.UUID | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    actor=Depends(require(FINANCE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[RefundOut]:
    """退款列表（只读，需 finance.view）。"""
    return await service.list_refunds(
        db, user_id=user_id, order_id=order_id, limit=limit, cursor=cursor
    )


@router.post("/admin/refunds/{refund_id}/confirm", response_model=RefundOut)
async def admin_confirm_refund(
    refund_id: uuid.UUID,
    body: RefundConfirmIn,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> RefundOut:
    """确认退款：撤销该订单来源的权益并冲正账本（需 settlement.manage）。"""
    return await service.confirm_refund(
        db, actor=actor, refund_id=refund_id, payload=body, request_id=request_id
    )


@router.get("/admin/reconciliations", response_model=CursorPage[ReconciliationItem])
async def admin_reconciliations(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    actor=Depends(require(FINANCE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ReconciliationItem]:
    """对账视图：本地支付尝试与渠道状态的差异（只读，需 finance.view）。"""
    return await service.list_reconciliations(db, limit=limit, cursor=cursor)


# --------------------------------------------------------------------------
# beta15：明细级收益分配、收益子账与预留（管理员入口，§10、§15.4、§15.5）
# --------------------------------------------------------------------------
@router.get("/admin/order-items/{order_item_id}/allocations", response_model=list[ComponentAllocationOut])
async def admin_order_item_allocations(
    order_item_id: uuid.UUID,
    actor=Depends(require(LEDGER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[ComponentAllocationOut]:
    """某订单明细的组件分配与受益人拆分（只读，需 ledger.view）。"""
    return await service.list_order_item_allocations(db, order_item_id)


@router.get("/admin/parties/{party_id}/settleable", response_model=SettleableBalanceOut)
async def admin_settleable_balance(
    party_id: uuid.UUID,
    currency: str = Query(min_length=3, max_length=3),
    actor=Depends(require(FINANCE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> SettleableBalanceOut:
    """某主体的可结算净额（只读，需 finance.view）。"""
    fen = await service.settleable_balance(db, party_id, currency)
    return SettleableBalanceOut(party_id=party_id, currency=currency, settleable_fen=fen)


@router.post("/admin/orders/{order_id}/accrual", response_model=AccrualResult)
async def admin_accrue_order(
    order_id: uuid.UUID,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AccrualResult:
    """对某订单执行初始计提（需 settlement.manage）；开关关闭时抛 NotEnabled。"""
    journal = await service.accrue_order_revenue(db, order_id)
    return AccrualResult(created=journal is not None, journal_id=journal.id if journal else None)


@router.post("/admin/allocations/{allocation_id}/reservations", response_model=ReservationOut)
async def admin_reserve_allocation(
    allocation_id: uuid.UUID,
    body: ReservationCreate,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ReservationOut:
    """按来源预留某分配的风险金额（需 settlement.manage）；开关关闭时抛 NotEnabled。"""
    reservation = await service.reserve_allocation(
        db, allocation_id, body.source_type, body.source_id, body.amount_fen
    )
    return ReservationOut.model_validate(reservation)


@router.post("/admin/reservations/{reservation_id}/release", response_model=ReservationOut)
async def admin_release_reservation(
    reservation_id: uuid.UUID,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ReservationOut:
    """释放预留（需 settlement.manage）；开关关闭时抛 NotEnabled。"""
    reservation = await service.release_reservation(db, reservation_id)
    return ReservationOut.model_validate(reservation)


@router.post("/admin/refunds/{refund_id}/reverse-revenue", response_model=ReversalResult)
async def admin_reverse_refund_revenue(
    refund_id: uuid.UUID,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ReversalResult:
    """对某退款执行收益冲回（需 settlement.manage）；开关关闭时抛 NotEnabled。"""
    created = await service.reverse_refund_revenue(db, refund_id)
    return ReversalResult(created=len(created))
