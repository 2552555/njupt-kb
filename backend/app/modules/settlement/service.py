"""settlement 业务规则（用户权责体系 §12、§14.1、§14.4、§15.5、§16.2/§16.3、§19 T19—T24）。

业务规则的唯一落点；`router.py` 只做解析、权限依赖与调用。

事务与锁顺序（§16.5，全模块统一，**不得**先取后段锁再回头取前段锁）：
    用户交易锁 → 订单 → receipt/claim/refund → grant 与会员段 → allocation（按 ID 排序）
    → 结算单/payout → ledger_accounts（按 ID 排序）。
本模块的批准流程先锁 allocation（按 ID 排序，经由 `commerce.reserve_allocation`），
再更新结算单与创建 payout，符合上述顺序；仅操作后段对象的流程不回头取前段锁。

资金能力开关（§2.5、§25.2）：`COMMERCE_ENABLED=False` 或 `COMMERCE_COMPLIANCE_GATE_CLOSED=True` 时，
一切写接口抛 `NotEnabled`，不返回假成功。没有任何默认结算条款：
`REVENUE_DEFAULT_CREATOR_BPS=0`、`SETTLEMENT_MIN_PAYOUT_FEN=0` 均表示「未配置」，
未配置时抛 `NotEnabled`，绝不套用 80/20、85/15 或任何示例比例。

不变量（§14.4）：
1. 累计已付＋批次锁定 ≤ 可支付净额；有退款/案件预留的金额不能进入新结算批次。
2. 付款在途（processing）禁止直接改金额。
3. `uncertain` 不释放占款；失败查明未付后才新建 attempt。
4. 一次事件不重复计提/冲回/开权/支付（`external_ref_key` 与各唯一键）。
5. 恢复备份后先核对 processing/uncertain 款项，不自动重放外部转账（见 `reconcile_pending_payouts`）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import LedgerEventType, PayoutStatus, SettlementStatementState
from app.core.errors import Conflict, InvalidInput, NotEnabled, NotFound
from app.core.pagination import CursorPage, encode_cursor, normalize_page_size, seek_descending
from app.modules.settlement.models import (
    Payout,
    PayoutAttempt,
    RecoveryClaim,
    RecoveryClaimState,
    SettlementItem,
    SettlementStatement,
)
from app.modules.settlement.schemas import (
    LedgerCorrectionOut,
    MySettlementStatementOut,
    PayoutAttemptOut,
    PayoutOut,
    RecoveryClaimOut,
    SettlementItemOut,
    SettlementStatementOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

__all__ = [
    "list_my_statements",
    "confirm_statement",
    "dispute_statement",
    "generate_statement",
    "approve_statement",
    "list_admin_statements",
    "list_admin_payouts",
    "create_payout_attempt",
    "reconcile_payout_attempt",
    "record_ledger_correction",
    "record_recovery_claims",
    "reconcile_pending_payouts",
]

# 预留状态与来源：与 commerce.models.AllocationReservation.state 的取值一致。
RESERVATION_ACTIVE = "active"
# 批次锁定预留：批准时按明细金额预留，使该金额不可再被其它批次使用（§14.4 不变量 5）。
RESERVATION_SOURCE_SETTLEMENT = "settlement"
# 争议冻结预留：提出结算异议时冻结争议金额（§13.1「收益及结算异议」）。
RESERVATION_SOURCE_DISPUTE = "dispute"

# 允许批准的前置状态（付款在途/已付/已争议不可再批准）。
_APPROVABLE_STATES = (
    SettlementStatementState.AWAITING_CONFIRMATION.value,
    SettlementStatementState.CONFIRMED.value,
)
# 允许贡献者确认/提出异议的状态。
_CONFIRMABLE_STATES = (
    SettlementStatementState.AWAITING_CONFIRMATION.value,
    SettlementStatementState.CONFIRMED.value,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# 开关与未配置条款（§2.5、§25.2、§1.3）
# --------------------------------------------------------------------------
def _require_enabled() -> None:
    """资金能力关闭时，一切写接口显式失败，不返回假成功。"""
    if not settings.COMMERCE_ENABLED or settings.COMMERCE_COMPLIANCE_GATE_CLOSED:
        raise NotEnabled(
            "结算与付款功能未启用",
            details={
                "reason": "COMMERCE_ENABLED/COMMERCE_COMPLIANCE_GATE_CLOSED",
                "commerce_enabled": settings.COMMERCE_ENABLED,
                "compliance_gate_closed": settings.COMMERCE_COMPLIANCE_GATE_CLOSED,
            },
        )


def _require_terms_configured() -> None:
    """分成比例未配置（0）时不得生成结算单，不套用任何示例比例（D09、§1.3）。"""
    if settings.REVENUE_DEFAULT_CREATOR_BPS <= 0:
        raise NotEnabled(
            "未配置结算分成比例，不能生成结算单",
            details={"reason": "REVENUE_DEFAULT_CREATOR_BPS=0", "config": "REVENUE_DEFAULT_CREATOR_BPS"},
        )


def _require_min_payout_configured() -> None:
    """最低付款额未配置（0）时不得进入付款流程，不套用任何默认门槛。"""
    if settings.SETTLEMENT_MIN_PAYOUT_FEN <= 0:
        raise NotEnabled(
            "未配置最低付款额，不能进入付款流程",
            details={"reason": "SETTLEMENT_MIN_PAYOUT_FEN=0", "config": "SETTLEMENT_MIN_PAYOUT_FEN"},
        )


# --------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------
def _snapshot_account(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None
    value = snapshot.get("account")
    return str(value) if value else None


def _mask_account(account: str | None) -> str | None:
    """脱敏账户：只显示尾号（§15.5「列表只提供脱敏信息」）。"""
    if not account:
        return None
    tail = account[-4:] if len(account) >= 4 else account
    return f"****{tail}"


async def _page(db: AsyncSession, stmt: Any, *, model: Any, limit: int | None, cursor: str | None):
    size = normalize_page_size(limit)
    stmt = seek_descending(db, stmt, model.created_at, model.id, cursor).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"ts": last.created_at.isoformat(), "id": str(last.id)})
    return rows, next_cursor, has_more


async def _allocation_available(db: AsyncSession, allocation: Any) -> int:
    """单个分配的可结算净额 = creator_fen − 已冲回 − active 预留（§14.4 不变量 5）。

    与 `commerce.service.settleable_balance` 同口径，但按 allocation 维度用于生成明细。
    """
    from app.modules.commerce.models import AllocationReservation, RefundBeneficiaryReversal

    reversed_total = int(
        await db.scalar(
            select(func.coalesce(func.sum(RefundBeneficiaryReversal.creator_reversal_fen), 0)).where(
                RefundBeneficiaryReversal.allocation_id == allocation.id
            )
        )
        or 0
    )
    reserved_total = int(
        await db.scalar(
            select(func.coalesce(func.sum(AllocationReservation.amount_fen), 0)).where(
                AllocationReservation.allocation_id == allocation.id,
                AllocationReservation.state == RESERVATION_ACTIVE,
            )
        )
        or 0
    )
    return allocation.creator_fen - reversed_total - reserved_total


async def _load_statement(db: AsyncSession, statement_id: uuid.UUID) -> SettlementStatement:
    statement = (
        await db.execute(select(SettlementStatement).where(SettlementStatement.id == statement_id))
    ).scalar_one_or_none()
    if statement is None:
        raise NotFound("结算单不存在")
    return statement


async def _load_payout(db: AsyncSession, payout_id: uuid.UUID, *, lock: bool = False) -> Payout:
    stmt = select(Payout).where(Payout.id == payout_id)
    if lock:
        stmt = stmt.with_for_update()
    payout = (await db.execute(stmt)).scalar_one_or_none()
    if payout is None:
        raise NotFound("付款计划不存在")
    return payout


# --------------------------------------------------------------------------
# 用户/贡献者侧（§16.2）
# --------------------------------------------------------------------------
async def _my_statement_out(db: AsyncSession, statement: SettlementStatement) -> MySettlementStatementOut:
    payout = (
        await db.execute(select(Payout).where(Payout.statement_version_id == statement.id))
    ).scalar_one_or_none()
    items = list(
        (
            await db.execute(
                select(SettlementItem)
                .where(SettlementItem.statement_id == statement.id)
                .order_by(SettlementItem.allocation_id)
            )
        )
        .scalars()
        .all()
    )
    base = MySettlementStatementOut.model_validate(statement)
    return base.model_copy(
        update={
            "masked_account": _mask_account(_snapshot_account(payout.payee_snapshot if payout else None)),
            "payout_state": payout.state if payout is not None else None,
            "items": [SettlementItemOut.model_validate(item) for item in items],
        }
    )


async def list_my_statements(
    db: AsyncSession, *, user: "User", limit: int | None, cursor: str | None
) -> CursorPage[MySettlementStatementOut]:
    """本人结算单与付款状态（只读，游标分页，账户脱敏）。只返回本人记录。"""
    stmt = select(SettlementStatement).where(SettlementStatement.payee_party_id == user.id)
    rows, next_cursor, has_more = await _page(
        db, stmt, model=SettlementStatement, limit=limit, cursor=cursor
    )
    items = [await _my_statement_out(db, row) for row in rows]
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def confirm_statement(
    db: AsyncSession,
    *,
    user: "User",
    statement_id: uuid.UUID,
    payload: Any,
    request_id: str,
) -> SettlementStatementOut:
    """本人合同收款主体确认明细（§16.2）：版本校验。"""
    _require_enabled()
    statement = (
        await db.execute(select(SettlementStatement).where(SettlementStatement.id == statement_id))
    ).scalar_one_or_none()
    # 非本人一律 404，不泄露他人结算单是否存在（§3.3）。
    if statement is None or statement.payee_party_id != user.id:
        raise NotFound("结算单不存在")
    if payload.expected_version != statement.version:
        raise Conflict(
            "结算单版本已变化，请刷新后重试",
            code="VERSION_CONFLICT",
            details={"expected": payload.expected_version, "actual": statement.version},
        )
    if statement.state not in _CONFIRMABLE_STATES:
        raise Conflict("当前状态不可确认", code="STATEMENT_NOT_CONFIRMABLE")
    statement.state = SettlementStatementState.CONFIRMED.value
    await record_audit(
        db,
        action="settlement.statement.confirmed",
        object_type="settlement_statement",
        object_id=statement.id,
        object_version=statement.version,
        actor_id=user.id,
        after={"state": statement.state},
        request_id=request_id,
    )
    await db.flush()
    # 状态更新会令 server 端 onupdate 的 updated_at 过期；显式刷新避免异步懒加载。
    await db.refresh(statement)
    return SettlementStatementOut.model_validate(statement)


async def dispute_statement(
    db: AsyncSession,
    *,
    user: "User",
    statement_id: uuid.UUID,
    payload: Any,
    request_id: str,
) -> SettlementStatementOut:
    """提出结算异议（§16.2）：关联争议行与金额，冻结争议金额并置 disputed（先回调查）。"""
    _require_enabled()
    from app.modules.commerce.service import reserve_allocation

    statement = (
        await db.execute(select(SettlementStatement).where(SettlementStatement.id == statement_id))
    ).scalar_one_or_none()
    if statement is None or statement.payee_party_id != user.id:
        raise NotFound("结算单不存在")
    if payload.expected_version != statement.version:
        raise Conflict(
            "结算单版本已变化，请刷新后重试",
            code="VERSION_CONFLICT",
            details={"expected": payload.expected_version, "actual": statement.version},
        )
    # 付款在途/已付/已锁批不得再改金额或状态（§14.4 不变量 2、§14.1「付款在途禁止直接改金额」）。
    if statement.state not in _CONFIRMABLE_STATES:
        raise Conflict("当前状态不可提出异议", code="STATEMENT_NOT_DISPUTABLE")
    # 按 allocation ID 排序冻结争议金额（§16.5 锁顺序）。
    for item in sorted(payload.items, key=lambda i: str(i.allocation_id)):
        await reserve_allocation(
            db, item.allocation_id, RESERVATION_SOURCE_DISPUTE, statement.id, item.amount_fen
        )
    statement.state = SettlementStatementState.DISPUTED.value
    await record_audit(
        db,
        action="settlement.statement.disputed",
        object_type="settlement_statement",
        object_id=statement.id,
        object_version=statement.version,
        actor_id=user.id,
        after={
            "state": statement.state,
            "items": [
                {"allocation_id": str(i.allocation_id), "amount_fen": i.amount_fen}
                for i in payload.items
            ],
        },
        reason=payload.reason,
        request_id=request_id,
    )
    await db.flush()
    await db.refresh(statement)
    return SettlementStatementOut.model_validate(statement)


# --------------------------------------------------------------------------
# 后台侧：生成与批准（§16.3）
# --------------------------------------------------------------------------
async def generate_statement(
    db: AsyncSession, *, actor: "User", payload: Any, request_id: str
) -> SettlementStatementOut:
    """生成结算草稿（§16.3）：按成熟和预留过滤，只纳入可结算净额。

    有退款/案件预留的金额（active `allocation_reservations`）不进入新批次；
    比例/最低额未配置时抛 `NotEnabled`；同一主体/期间/币种每次生成创建新版本，旧版本保留。
    """
    _require_enabled()
    _require_terms_configured()
    _require_min_payout_configured()

    from app.modules.commerce.models import OrderItemAllocation
    from app.modules.commerce.service import settleable_balance

    payee = payload.payee_party_id
    currency = payload.currency
    # 主体级可结算净额（已扣 active 预留）作为准入门槛（§14.4 不变量 5）。
    net = await settleable_balance(db, payee, currency)
    if net <= 0:
        raise Conflict(
            "无可结算金额",
            code="NO_SETTLEABLE_AMOUNT",
            details={"payee_party_id": str(payee), "currency": currency},
        )

    allocations = list(
        (
            await db.execute(
                select(OrderItemAllocation)
                .where(OrderItemAllocation.beneficiary_party_id == payee)
                .order_by(OrderItemAllocation.id)
            )
        )
        .scalars()
        .all()
    )
    items: list[tuple[uuid.UUID, int]] = []
    for allocation in allocations:
        available = await _allocation_available(db, allocation)
        if available > 0:
            items.append((allocation.id, available))
    if not items:
        raise Conflict(
            "无可结算金额",
            code="NO_SETTLEABLE_AMOUNT",
            details={"payee_party_id": str(payee), "currency": currency},
        )

    last_version = await db.scalar(
        select(func.max(SettlementStatement.version)).where(
            SettlementStatement.payee_party_id == payee,
            SettlementStatement.period == payload.period,
            SettlementStatement.currency == currency,
        )
    )
    version = int(last_version or 0) + 1
    gross = sum(amount for _, amount in items)
    statement = SettlementStatement(
        payee_party_id=payee,
        period=payload.period,
        currency=currency,
        version=version,
        state=SettlementStatementState.AWAITING_CONFIRMATION.value,
        gross_fen=gross,
        tax_fen=0,
        net_fen=gross,
    )
    db.add(statement)
    await db.flush()
    for allocation_id, amount in items:
        db.add(
            SettlementItem(statement_id=statement.id, allocation_id=allocation_id, amount_fen=amount)
        )
    await db.flush()
    await record_audit(
        db,
        action="settlement.statement.generated",
        object_type="settlement_statement",
        object_id=statement.id,
        object_version=version,
        actor_id=actor.id,
        after={
            "payee_party_id": str(payee),
            "period": payload.period,
            "currency": currency,
            "version": version,
            "gross_fen": gross,
            "items": len(items),
        },
        request_id=request_id,
    )
    await db.flush()
    await db.refresh(statement)
    return SettlementStatementOut.model_validate(statement)


async def approve_statement(
    db: AsyncSession, *, actor: "User", statement_id: uuid.UUID, payload: Any, request_id: str
) -> SettlementStatementOut:
    """批准并锁批（§16.3）：财务权限与版本校验；锁入批次后金额不可再被其它批次使用。

    锁顺序（§16.5）：先按 allocation ID 排序预留（锁 allocation），再更新结算单/创建 payout，
    不先取结算单锁再回头取 allocation 锁。
    """
    _require_enabled()
    from app.modules.commerce.service import reserve_allocation

    statement = await _load_statement(db, statement_id)
    if payload.expected_version != statement.version:
        raise Conflict(
            "结算单版本已变化，请刷新后重试",
            code="VERSION_CONFLICT",
            details={"expected": payload.expected_version, "actual": statement.version},
        )
    if statement.state not in _APPROVABLE_STATES:
        raise Conflict("当前状态不可批准", code="STATEMENT_NOT_APPROVABLE")

    items = list(
        (
            await db.execute(
                select(SettlementItem)
                .where(SettlementItem.statement_id == statement.id)
                .order_by(SettlementItem.allocation_id)
            )
        )
        .scalars()
        .all()
    )
    if not items:
        raise Conflict("结算单没有明细", code="STATEMENT_EMPTY")

    # 锁 allocation（按 ID 排序）并预留：超出可结算净额时 reserve_allocation 抛
    # INSUFFICIENT_SETTLEABLE_AMOUNT，保证累计锁定＋已付不超可付净额（§14.4 不变量 5）。
    for item in sorted(items, key=lambda i: str(i.allocation_id)):
        await reserve_allocation(
            db, item.allocation_id, RESERVATION_SOURCE_SETTLEMENT, statement.id, item.amount_fen
        )

    snapshot = payload.payee_snapshot if payload.payee_snapshot is not None else {}
    before_state = statement.state
    statement.state = SettlementStatementState.APPROVED.value
    payout = Payout(
        statement_version_id=statement.id,
        payee_snapshot=snapshot,
        amount_fen=statement.net_fen,
        state=PayoutStatus.CREATED.value,
    )
    db.add(payout)
    await db.flush()
    await record_audit(
        db,
        action="settlement.statement.approved",
        object_type="settlement_statement",
        object_id=statement.id,
        object_version=statement.version,
        actor_id=actor.id,
        before={"state": before_state},
        after={"state": statement.state, "payout_id": str(payout.id), "net_fen": statement.net_fen},
        request_id=request_id,
    )
    await db.flush()
    await db.refresh(statement)
    return SettlementStatementOut.model_validate(statement)


# --------------------------------------------------------------------------
# 后台侧：付款与核对（§16.3、§12.3、§14.4 不变量 2/3）
# --------------------------------------------------------------------------
async def create_payout_attempt(
    db: AsyncSession, *, actor: "User", payout_id: uuid.UUID, payload: Any, request_id: str
) -> PayoutAttemptOut:
    """预留对外付款操作（§16.3）：准入和账户快照核验；最低付款额未配置时抛 NotEnabled。"""
    _require_enabled()
    _require_min_payout_configured()

    payout = await _load_payout(db, payout_id, lock=True)
    # 已成功/在途/不确定的付款计划不得重复创建 attempt（§14.4 不变量 3）。
    if payout.state not in (PayoutStatus.CREATED.value, PayoutStatus.FAILED.value):
        raise Conflict("当前付款计划状态不可新建付款操作", code="PAYOUT_NOT_ATTEMPTABLE")
    if payout.amount_fen < settings.SETTLEMENT_MIN_PAYOUT_FEN:
        raise Conflict(
            "付款金额低于最低付款额",
            code="BELOW_MIN_PAYOUT",
            details={"amount_fen": payout.amount_fen, "min_fen": settings.SETTLEMENT_MIN_PAYOUT_FEN},
        )
    # 账户快照核验：无冻结账户不得发起对外付款。
    if not _snapshot_account(payout.payee_snapshot):
        raise Conflict("付款账户快照缺失", code="PAYOUT_ACCOUNT_MISSING")

    last_no = await db.scalar(
        select(func.max(PayoutAttempt.attempt_no)).where(PayoutAttempt.payout_id == payout.id)
    )
    attempt = PayoutAttempt(
        payout_id=payout.id,
        attempt_no=int(last_no or 0) + 1,
        state=PayoutStatus.PROCESSING.value,
    )
    db.add(attempt)
    # 付款在途：禁止直接改金额（§14.4 不变量 2）。
    payout.state = PayoutStatus.PROCESSING.value
    await db.flush()
    await record_audit(
        db,
        action="settlement.payout.attempt_created",
        object_type="payout_attempt",
        object_id=attempt.id,
        object_version=attempt.attempt_no,
        actor_id=actor.id,
        after={
            "payout_id": str(payout.id),
            "attempt_no": attempt.attempt_no,
            "state": attempt.state,
            "amount_fen": payout.amount_fen,
        },
        reason=payload.note,
        request_id=request_id,
    )
    await db.flush()
    await db.refresh(attempt)
    return PayoutAttemptOut.model_validate(attempt)


async def reconcile_payout_attempt(
    db: AsyncSession, *, actor: "User", attempt_id: uuid.UUID, payload: Any, request_id: str
) -> PayoutAttemptOut:
    """核对付款结果（§16.3）：真实证据必填；成功必须给出真实转出唯一键。

    `uncertain` 持续占款、不释放预留（§14.4 不变量 3、§17.4）；失败查明未付后才新建 attempt。
    """
    _require_enabled()
    attempt = (
        await db.execute(
            select(PayoutAttempt).where(PayoutAttempt.id == attempt_id).with_for_update()
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise NotFound("付款尝试不存在")
    if attempt.state not in (PayoutStatus.PROCESSING.value, PayoutStatus.CREATED.value, PayoutStatus.UNCERTAIN.value):
        raise Conflict("当前付款尝试状态不可核对", code="ATTEMPT_NOT_RECONCILABLE")
    if not payload.evidence_ref or not payload.evidence_ref.strip():
        raise InvalidInput("核对付款结果必须提供真实证据", details={"reason": "evidence_ref_required"})

    payout = await _load_payout(db, attempt.payout_id, lock=True)

    if payload.result == "succeeded":
        key = (payload.external_ref_key or "").strip()
        if not key:
            raise InvalidInput("成功核对必须提供真实转出唯一键", details={"reason": "external_ref_key_required"})
        duplicate = await db.scalar(
            select(PayoutAttempt).where(
                PayoutAttempt.external_ref_key == key, PayoutAttempt.id != attempt.id
            )
        )
        if duplicate is not None:
            # 真实转出键唯一：同一次转出不得重复记录、不重复付款（§14.4 不变量 3）。
            raise Conflict(
                "真实转出唯一键已被使用，疑似重复付款",
                code="DUPLICATE_PAYOUT_REFERENCE",
                details={"external_ref_key": key},
            )
        attempt.external_ref_key = key
        attempt.state = PayoutStatus.SUCCEEDED.value
        payout.state = PayoutStatus.SUCCEEDED.value
        statement = await _load_statement(db, payout.statement_version_id)
        statement.state = SettlementStatementState.PAID.value
    elif payload.result == "failed":
        attempt.state = PayoutStatus.FAILED.value
        payout.state = PayoutStatus.FAILED.value
    else:
        # uncertain：外部结果未知，持续占款不释放（§12.3、§17.4）。
        attempt.state = PayoutStatus.UNCERTAIN.value
        payout.state = PayoutStatus.UNCERTAIN.value

    attempt.evidence_ref = payload.evidence_ref
    await db.flush()
    await record_audit(
        db,
        action="settlement.payout.reconciled",
        object_type="payout_attempt",
        object_id=attempt.id,
        object_version=attempt.attempt_no,
        actor_id=actor.id,
        after={
            "payout_id": str(payout.id),
            "state": attempt.state,
            "external_ref_key": attempt.external_ref_key,
            "evidence_ref": attempt.evidence_ref,
        },
        reason=payload.reason,
        request_id=request_id,
    )
    await db.flush()
    await db.refresh(attempt)
    return PayoutAttemptOut.model_validate(attempt)


async def reconcile_pending_payouts(db: AsyncSession) -> list[dict[str, Any]]:
    """恢复备份后核对 processing/uncertain 款项（§17.4）：只读，不自动重放外部转账动作。

    返回待人工核对清单；本函数不发起任何外部转账，也不修改付款状态。
    """
    rows = (
        await db.execute(
            select(PayoutAttempt, Payout)
            .join(Payout, Payout.id == PayoutAttempt.payout_id)
            .where(
                PayoutAttempt.state.in_(
                    [PayoutStatus.PROCESSING.value, PayoutStatus.UNCERTAIN.value]
                )
            )
            .order_by(PayoutAttempt.created_at, PayoutAttempt.id)
        )
    ).all()
    return [
        {
            "payout_id": str(payout.id),
            "attempt_id": str(attempt.id),
            "attempt_no": attempt.attempt_no,
            "state": attempt.state,
            "external_ref_key": attempt.external_ref_key,
            "evidence_ref": attempt.evidence_ref,
            "amount_fen": payout.amount_fen,
            "requires_manual_reconciliation": True,
        }
        for attempt, payout in rows
    ]


# --------------------------------------------------------------------------
# 追偿（§11.5、§19 T21）
# --------------------------------------------------------------------------
async def _paid_reference_for_allocation(db: AsyncSession, allocation_id: uuid.UUID) -> str | None:
    """某分配是否已付：返回已付结算单引用（保留已付历史，不改写已付记录）。"""
    row = (
        await db.execute(
            select(SettlementItem.statement_id)
            .join(SettlementStatement, SettlementStatement.id == SettlementItem.statement_id)
            .where(
                SettlementItem.allocation_id == allocation_id,
                SettlementStatement.state == SettlementStatementState.PAID.value,
            )
            .order_by(SettlementItem.created_at, SettlementItem.id)
        )
    ).scalars().first()
    return str(row) if row is not None else None


async def record_recovery_claims(
    db: AsyncSession,
    *,
    refund_id: uuid.UUID,
    legal_basis_ref: str,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> list[RecoveryClaimOut]:
    """已付报酬随后退款：已付历史保留，追偿单独记录（§11.5、§19 T21）。

    仅对**已真实支付**的分配建立追偿；未付部分由账本冲回处理，不在此建追偿。
    无依据不得自动扣：`legal_basis_ref` 必填。同一 (allocation, refund) 幂等。
    """
    _require_enabled()
    if not legal_basis_ref or not legal_basis_ref.strip():
        raise InvalidInput(
            "追偿必须提供依据，禁止无依据自动扣",
            details={"reason": "legal_basis_ref_required"},
        )

    from app.modules.commerce.models import (
        RefundBeneficiaryReversal,
        RefundComponentAllocation,
        RefundItem,
    )

    rows = list(
        (
            await db.execute(
                select(RefundBeneficiaryReversal)
                .join(
                    RefundComponentAllocation,
                    RefundComponentAllocation.id
                    == RefundBeneficiaryReversal.refund_component_allocation_id,
                )
                .join(RefundItem, RefundItem.id == RefundComponentAllocation.refund_item_id)
                .where(RefundItem.refund_id == refund_id)
                .order_by(RefundBeneficiaryReversal.allocation_id)
            )
        )
        .scalars()
        .all()
    )

    created: list[RecoveryClaimOut] = []
    for reversal in rows:
        amount = reversal.creator_reversal_fen
        if amount <= 0:
            continue
        paid_reference = await _paid_reference_for_allocation(db, reversal.allocation_id)
        if paid_reference is None:
            continue  # 未付部分走账本冲回，不建追偿
        existing = (
            await db.execute(
                select(RecoveryClaim).where(
                    RecoveryClaim.allocation_id == reversal.allocation_id,
                    RecoveryClaim.refund_id == refund_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            created.append(RecoveryClaimOut.model_validate(existing))
            continue
        claim = RecoveryClaim(
            allocation_id=reversal.allocation_id,
            refund_id=refund_id,
            paid_reference=paid_reference,
            amount_fen=amount,
            legal_basis_ref=legal_basis_ref,
            state=RecoveryClaimState.OPEN.value,
        )
        db.add(claim)
        await db.flush()
        await db.refresh(claim)
        created.append(RecoveryClaimOut.model_validate(claim))

    if created and actor_id is not None:
        await record_audit(
            db,
            action="settlement.recovery.recorded",
            object_type="refund",
            object_id=refund_id,
            actor_id=actor_id,
            after={"claims": len(created), "legal_basis_ref": legal_basis_ref},
            request_id=request_id,
        )
        await db.flush()
    return created


# --------------------------------------------------------------------------
# 账务更正（§12.1、§16.3）：关联原记录，不提供直接改余额
# --------------------------------------------------------------------------
async def record_ledger_correction(
    db: AsyncSession, *, actor: "User", payload: Any, request_id: str
) -> LedgerCorrectionOut:
    """提出及确认账务更正（§16.3）：新增反向分录关联原 journal，不提供直接改余额。"""
    _require_enabled()
    from app.modules.commerce.models import LedgerJournal, LedgerLine

    original = await db.get(LedgerJournal, payload.original_journal_id)
    if original is None:
        raise NotFound("原账本事件不存在")
    event_key = f"correction:{original.id}"
    existing = (
        await db.execute(select(LedgerJournal).where(LedgerJournal.event_key == event_key))
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("该账本事件已更正", code="CORRECTION_ALREADY_EXISTS")

    lines = list(
        (await db.execute(select(LedgerLine).where(LedgerLine.journal_id == original.id)))
        .scalars()
        .all()
    )
    if not lines:
        raise Conflict("原账本事件没有分录，无法更正", code="NOTHING_TO_CORRECT")

    correction = LedgerJournal(
        event_key=event_key,
        event_type=LedgerEventType.CORRECTION.value,
        source_id=original.id,
        reverses_id=original.id,
        occurred_at=_now(),
    )
    db.add(correction)
    await db.flush()
    reversed_fen = 0
    for line in lines:
        # 反向分录：借贷互换，金额守恒（原 journal 借贷相等 → 反向仍相等）。
        db.add(
            LedgerLine(
                journal_id=correction.id,
                account_id=line.account_id,
                allocation_id=line.allocation_id,
                debit_fen=line.credit_fen,
                credit_fen=line.debit_fen,
            )
        )
        reversed_fen += line.debit_fen
    await db.flush()
    await record_audit(
        db,
        action="settlement.ledger.corrected",
        object_type="ledger_journal",
        object_id=correction.id,
        actor_id=actor.id,
        after={
            "original_journal_id": str(original.id),
            "reversed_fen": reversed_fen,
        },
        reason=payload.reason,
        request_id=request_id,
    )
    await db.flush()
    return LedgerCorrectionOut(
        journal_id=correction.id,
        original_journal_id=original.id,
        event_type=correction.event_type,
        reason=payload.reason,
        reversed_fen=reversed_fen,
    )


# --------------------------------------------------------------------------
# 后台侧：查询（§16.3）
# --------------------------------------------------------------------------
async def list_admin_statements(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[SettlementStatementOut]:
    rows, next_cursor, has_more = await _page(
        db, select(SettlementStatement), model=SettlementStatement, limit=limit, cursor=cursor
    )
    return CursorPage(
        items=[SettlementStatementOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_admin_payouts(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[PayoutOut]:
    rows, next_cursor, has_more = await _page(
        db, select(Payout), model=Payout, limit=limit, cursor=cursor
    )
    return CursorPage(
        items=[PayoutOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_payout_attempts(
    db: AsyncSession, *, payout_id: uuid.UUID, limit: int | None, cursor: str | None,
) -> CursorPage[PayoutAttemptOut]:
    await _load_payout(db, payout_id)
    rows, next_cursor, has_more = await _page(db,
        select(PayoutAttempt).where(PayoutAttempt.payout_id == payout_id),
        model=PayoutAttempt, limit=limit, cursor=cursor)
    return CursorPage(items=[PayoutAttemptOut.model_validate(row) for row in rows],
                      next_cursor=next_cursor, has_more=has_more)
