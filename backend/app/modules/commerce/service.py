"""commerce 应用服务：订单、支付、退款、对账、账本、结算与提现。

设计文档 §24.2、§24.3、§25.2；契约见 docs/INTERFACES.md 的 `## commerce`。

核心不变量（§24.3）：
- 前端成功页不作为付款事实；只接受验签通过的通知，并校验订单、金额、币种与渠道交易 ID。
- 重复回调不重复授予权益（渠道交易 ID 唯一 + 支付尝试终态幂等）。
- 外部支付与本地库不是原子事务，允许 `PENDING_CONFIRMATION` 的可恢复状态。
- 退款未确认前记录处理中；确认后只撤销来自该订单的权益，不误撤其他来源。
- 账本借贷平衡、带业务引用与冲正关联；同一业务结算幂等，更正走反向分录。
- 待结算 / 冻结 / 可提现 / 已提现分开，余额从账本核验，不使用 `users.balance`。
- 分成比例、贡献公式、退款追溯与最低提现额均待确认（D16）：做成配置项，默认未配置，
  未配置时结算/提现接口抛 `NotEnabled`，不采用任何示例比例。

beta15 增量（《用户权责、内容治理与授权收益体系设计文档》§10、§11、§14.4、§15.4、§15.5、§16.5）：

事务与锁顺序（§16.5，全模块统一）：
    用户交易锁 → 订单 → receipt/claim/refund → grant 与会员段 → allocation（按 ID 排序）
    → 结算单/payout → ledger_accounts（按 ID 排序）。
仅操作后段对象的流程不得取得后段锁后再回头取前段锁；需要关联前段时从完整顺序开始。
本文件内 `allocate_order_item` / `accrue_revenue` / `reverse_revenue_for_refund` / `reserve_allocation`
均只做 flush，不 commit；提交与回滚由调用方（`get_db` 请求事务）负责。

金额口径：新增列一律整数分（`*_fen`）；既有 `Numeric(18,4)` 的元金额通过 `_fen()` 换算。
`ledger_journals.event_key` 全局唯一，保证一次事件不重复计提／冲回（§14.4 不变量 3）。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import idempotency as idem
from app.core.audit import record_audit
from app.core.deps import parse_uuid
from app.core.enums import (
    DomainEventType,
    EntitlementSource,
    LedgerAccount,
    LedgerBucket,
    LedgerDirection,
    LedgerEventType,
    OrderStatus,
    PaymentAttemptStatus,
    PublicationStatus,
    RefundStatus,
    Role,
    SettlementStatus,
    WithdrawalStatus,
)
from app.core.errors import Conflict, Forbidden, InvalidInput, NotEnabled, NotFound
from app.core.events import emit_event
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.modules.commerce import channels
from app.modules.commerce.models import (
    AllocationReservation,
    LedgerAccountRecord,
    LedgerEntry,
    LedgerJournal,
    LedgerLine,
    Order,
    OrderItem,
    OrderItemAllocation,
    OrderItemComponentAllocation,
    PaymentAttempt,
    Price,
    Product,
    Refund,
    RefundBeneficiaryReversal,
    RefundComponentAllocation,
    RefundItem,
    Settlement,
    Withdrawal,
)
from app.modules.commerce.schemas import (
    BeneficiaryAllocationOut,
    ComponentAllocationOut,
    LedgerEntryOut,
    OrderCreate,
    OrderItemOut,
    OrderOut,
    PriceOut,
    ProductOut,
    ReconciliationItem,
    RefundConfirmIn,
    RefundCreate,
    RefundOut,
    SettlementOut,
    SettlementRunIn,
    SettlementRunOut,
    WithdrawalCreate,
    WithdrawalOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.access.models import Entitlement
    from app.modules.identity.models import User

# --- 订单与幂等 ---
DEFAULT_ORDER_TTL_MINUTES = 30
ORDER_SCOPE = "commerce.order"
REFUND_SCOPE = "commerce.refund"
WITHDRAWAL_SCOPE = "commerce.withdrawal"

# --- 待确认规则（D16）：默认未配置，绝不使用示例比例 ---
CREATOR_POOL_RATIO_ENV = "COMMERCE_CREATOR_POOL_RATIO"
MIN_WITHDRAWAL_ENV = "COMMERCE_MIN_WITHDRAWAL"

MONEY_QUANT = Decimal("0.0001")

# --- beta15 明细级收益分配与收益子账（§10、§11、§15.4、§15.5）---
BPS_DENOMINATOR = 10000
# 退款能力版本：旧订单默认 FULL_ORDER_ONLY，新订单快照记录版本（§11.3、§18.2）。
REFUND_CAPABILITY_FULL_ORDER = "FULL_ORDER_ONLY"
REFUND_CAPABILITY_ORDER_ITEMS = "ORDER_ITEMS_V1"
REFUND_SCOPE_FULL_ORDER = "FULL_ORDER"
REFUND_SCOPE_ORDER_ITEMS = "ORDER_ITEMS"
# 预留状态：无对应核心枚举，取值在服务层维护（见 models.AllocationReservation.state）。
RESERVATION_ACTIVE = "active"
RESERVATION_RELEASED = "released"


# --------------------------------------------------------------------------
# 通用小工具与阶段开关
# --------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _require_enabled() -> None:
    """阶段开关关闭时，一切会产生资金副作用的写接口显式失败（§25.2）。"""
    if not settings.COMMERCE_ENABLED:
        raise NotEnabled("商业功能未启用", details={"reason": "COMMERCE_ENABLED=false"})


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _env_decimal(name: str) -> Decimal | None:
    """从环境读取待确认的金额/比例配置；未配置返回 None（不设默认值，D16）。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        return None


def creator_pool_ratio() -> Decimal | None:
    """创作者池比例（D16 待确认）。未配置返回 None，结算接口据此抛 NotEnabled。"""
    return _env_decimal(CREATOR_POOL_RATIO_ENV)


def min_withdrawal_amount(currency: str) -> Decimal | None:
    """最低提现额（D16 待确认）。未配置返回 None，提现接口据此抛 NotEnabled。"""
    del currency  # 首期单币种配置；多币种最低额待确认
    return _env_decimal(MIN_WITHDRAWAL_ENV)


# --------------------------------------------------------------------------
# beta15 金额与分配工具（§10.2：守恒、最大余数法、确定性顺序）
# --------------------------------------------------------------------------
def _fen(amount: Decimal | int | str) -> int:
    """元金额（`Numeric(18,4)`）换算为整数分；四舍五入到分。"""
    value = Decimal(str(amount))
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _largest_remainder(total: int, weights: list[tuple[str, int]]) -> dict[str, int]:
    """按权重把整数 `total` 拆成整数份（最大余数法，§10.2）。

    先向下取整，再把剩余分按「余数降序、key 升序」依次 +1；同余数按不可变 key 顺序，
    保证重跑顺序与结果一致。权重之和为 0 且 total 非 0 时抛输入错误。
    """
    result: dict[str, int] = {key: 0 for key, _ in weights}
    positive = [(key, weight) for key, weight in weights if weight > 0]
    weight_sum = sum(weight for _, weight in positive)
    if weight_sum <= 0:
        if total != 0:
            raise InvalidInput("组件权重之和必须为正", details={"total": total})
        return result
    remainders: list[tuple[int, str]] = []
    for key, weight in positive:
        share, remainder = divmod(total * weight, weight_sum)
        result[key] = share
        remainders.append((remainder, key))
    leftover = total - sum(result.values())
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for index in range(leftover):
        result[remainders[index % len(remainders)][1]] += 1
    return result


@dataclass(frozen=True)
class BeneficiaryShare:
    """组件内某一受益人的已确认份额（基点，合计应为 10000）。"""

    beneficiary_party_id: uuid.UUID
    share_bps: int


@dataclass(frozen=True)
class ComponentSpec:
    """订单明细中一个组件的冻结分配输入（§10.2）。

    `weight` 为该组件在明细内的相对权重（组合商品按权重拆分明细实收）；
    `creator_bps` 为贡献者分成基点；`deduction_fen` 为经合同允许的分成前扣减。
    """

    component_key: str
    weight: int
    creator_bps: int
    deduction_fen: int = 0
    beneficiaries: tuple[BeneficiaryShare, ...] = ()
    agreement_version_id: uuid.UUID | None = None
    asset_version_id: uuid.UUID | None = None


def _component_specs_from_snapshot(snapshot: dict[str, Any] | None) -> list[ComponentSpec]:
    """把冻结的组成快照（JSON）解析为组件分配输入；无组成时返回空列表（旧订单）。"""
    if not snapshot:
        return []
    specs: list[ComponentSpec] = []
    for raw in snapshot.get("components", []):
        shares = tuple(
            BeneficiaryShare(
                beneficiary_party_id=uuid.UUID(str(item["party_id"])),
                share_bps=int(item.get("share_bps", 0)),
            )
            for item in raw.get("beneficiaries", [])
        )
        agreement_version_id = raw.get("agreement_version_id")
        asset_version_id = raw.get("asset_version_id")
        specs.append(
            ComponentSpec(
                component_key=str(raw["component_key"]),
                weight=int(raw.get("weight", 0)),
                creator_bps=int(raw.get("creator_bps", 0)),
                deduction_fen=int(raw.get("deduction_fen", 0)),
                beneficiaries=shares,
                agreement_version_id=uuid.UUID(str(agreement_version_id))
                if agreement_version_id
                else None,
                asset_version_id=uuid.UUID(str(asset_version_id)) if asset_version_id else None,
            )
        )
    return specs


def _order_items_refund_supported() -> bool:
    """明细退款能力是否可用（§16.3、§18.3）。

    按来源撤权、组件级访问控制、追偿与渠道多次退款尚未实现，因此即便商业开关与合规门槛
    打开也返回 False，不静默降级为整单退款。
    """
    return False


def _refund_capability_version() -> str:
    """下单时冻结的退款能力版本（§11.3、§18.2）：明细退款未启用时为 FULL_ORDER_ONLY。"""
    return (
        REFUND_CAPABILITY_ORDER_ITEMS
        if _order_items_refund_supported()
        else REFUND_CAPABILITY_FULL_ORDER
    )


# --------------------------------------------------------------------------
# 游标辅助（§25.1）
# --------------------------------------------------------------------------
def _cursor_of(obj: Any) -> str:
    return encode_cursor({"t": _aware(obj.created_at).isoformat(), "i": str(obj.id)})


def _apply_time_cursor(stmt: Any, model: Any, cursor: str | None) -> Any:
    if not cursor:
        return stmt
    data = decode_cursor(cursor)
    try:
        ts = datetime.fromisoformat(str(data["t"]))
        cid = uuid.UUID(str(data["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return stmt.where(
        or_(model.created_at < ts, and_(model.created_at == ts, model.id < cid))
    )


# --------------------------------------------------------------------------
# 账本（§24.3）：平衡分录 + 冲正关联 + 业务幂等
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Entry:
    account: str
    account_id: uuid.UUID | None
    direction: str
    amount: Decimal
    memo: str | None
    dedupe_key: str


async def _post_balanced(
    db: AsyncSession,
    *,
    currency: str,
    business_type: str,
    business_id: uuid.UUID | None,
    entries: list[_Entry],
    settlement_id: uuid.UUID | None = None,
) -> list[LedgerEntry]:
    """写入一组借贷平衡的分录；金额为 0 或 `dedupe_key` 已存在的分录跳过（幂等）。"""
    debits = sum((e.amount for e in entries if e.direction == LedgerDirection.DEBIT.value), Decimal(0))
    credits = sum((e.amount for e in entries if e.direction == LedgerDirection.CREDIT.value), Decimal(0))
    if debits != credits:
        raise Conflict("账本分录不平衡", code="LEDGER_UNBALANCED")

    keys = [e.dedupe_key for e in entries]
    existing = set(
        (await db.execute(select(LedgerEntry.dedupe_key).where(LedgerEntry.dedupe_key.in_(keys))))
        .scalars()
        .all()
    )
    group_id = uuid.uuid4()
    created: list[LedgerEntry] = []
    for entry in entries:
        if entry.amount <= 0 or entry.dedupe_key in existing:
            continue
        row = LedgerEntry(
            account=entry.account,
            account_id=entry.account_id,
            direction=entry.direction,
            amount=entry.amount,
            currency=currency,
            business_type=business_type,
            business_id=business_id,
            entry_group_id=group_id,
            settlement_id=settlement_id,
            memo=entry.memo,
            dedupe_key=entry.dedupe_key,
        )
        db.add(row)
        created.append(row)
    if created:
        await db.flush()
    return created


async def _reverse_entries(
    db: AsyncSession,
    *,
    originals: list[LedgerEntry],
    business_type: str,
    business_id: uuid.UUID | None,
    reason: str,
) -> list[LedgerEntry]:
    """用反向分录更正已入账金额，不修改历史记录（§24.3）。"""
    created: list[LedgerEntry] = []
    for original in originals:
        dedupe_key = f"reverse:{original.id}"
        if (
            await db.execute(select(LedgerEntry.id).where(LedgerEntry.dedupe_key == dedupe_key))
        ).scalar_one_or_none() is not None:
            continue
        opposite = (
            LedgerDirection.CREDIT.value
            if original.direction == LedgerDirection.DEBIT.value
            else LedgerDirection.DEBIT.value
        )
        row = LedgerEntry(
            account=original.account,
            account_id=original.account_id,
            direction=opposite,
            amount=original.amount,
            currency=original.currency,
            business_type=business_type,
            business_id=business_id,
            entry_group_id=uuid.uuid4(),
            reverses_entry_id=original.id,
            memo=reason,
            dedupe_key=dedupe_key,
        )
        db.add(row)
        created.append(row)
    if created:
        await db.flush()
    return created


async def _ledger_balance(
    db: AsyncSession, *, account: str, account_id: uuid.UUID | None, currency: str
) -> Decimal:
    """从账本核验余额：贷方合计 − 借方合计（§24.3）。"""
    credits = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account == account,
            LedgerEntry.account_id == account_id,
            LedgerEntry.currency == currency,
            LedgerEntry.direction == LedgerDirection.CREDIT.value,
        )
    )
    debits = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account == account,
            LedgerEntry.account_id == account_id,
            LedgerEntry.currency == currency,
            LedgerEntry.direction == LedgerDirection.DEBIT.value,
        )
    )
    return Decimal(str(credits or 0)) - Decimal(str(debits or 0))


# --------------------------------------------------------------------------
# 产品（只读，§24.2）
# --------------------------------------------------------------------------
async def list_products(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[ProductOut]:
    """已上架产品与其生效价格；只读，不涉及资金，关闭阶段仍可用。"""
    size = normalize_page_size(limit)
    stmt = (
        select(Product)
        .where(Product.publication_status == PublicationStatus.PUBLISHED.value)
        .order_by(Product.created_at.desc(), Product.id.desc())
    )
    stmt = _apply_time_cursor(stmt, Product, cursor)
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]

    price_map: dict[uuid.UUID, Price] = {}
    if items:
        price_stmt = (
            select(Price)
            .where(Price.product_id.in_([p.id for p in items]), Price.is_active.is_(True))
            .order_by(Price.version.desc())
        )
        for price in (await db.execute(price_stmt)).scalars().all():
            price_map.setdefault(price.product_id, price)

    out = []
    for product in items:
        dto = ProductOut.model_validate(product)
        price = price_map.get(product.id)
        if price is not None:
            dto = dto.model_copy(update={"price": PriceOut.model_validate(price)})
        out.append(dto)

    next_cursor = _cursor_of(items[-1]) if has_more and items else None
    return CursorPage[ProductOut](items=out, next_cursor=next_cursor, has_more=has_more)


async def _active_price(db: AsyncSession, product_id: uuid.UUID) -> Price | None:
    stmt = (
        select(Price)
        .where(Price.product_id == product_id, Price.is_active.is_(True))
        .order_by(Price.version.desc())
    )
    return (await db.execute(stmt)).scalars().first()


# --------------------------------------------------------------------------
# 订单（§24.2）
# --------------------------------------------------------------------------
async def create_order(
    db: AsyncSession,
    *,
    user: "User",
    payload: OrderCreate,
    request_id: str,
    idempotency_key: str | None,
) -> OrderOut:
    """创建订单并生成不可变快照；提供渠道时同时发起支付。"""
    _require_enabled()
    key = idem.normalize_key(idempotency_key)
    scope = f"{ORDER_SCOPE}:{user.id}"
    req_payload = {
        "product_ids": [str(pid) for pid in payload.product_ids],
        "channel": payload.channel,
    }
    cached = await idem.lookup(db, scope=scope, key=key, payload=req_payload, actor_id=user.id)
    if cached is not None:
        return await _order_out(db, await _get_order(db, uuid.UUID(cached["order_id"])))

    product_ids = list(dict.fromkeys(payload.product_ids))
    products = list(
        (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
    )
    by_id = {product.id: product for product in products}
    if len(by_id) != len(product_ids) or any(
        by_id[pid].publication_status != PublicationStatus.PUBLISHED.value for pid in product_ids
    ):
        raise NotFound("产品不存在或未上架")
    if any(by_id[pid].grant_scope_id is None for pid in product_ids):
        # 权益必须有明确作用对象（access.entitlements.scope_id 非空）
        raise Conflict("产品未配置授予权益的范围", code="PRODUCT_SCOPE_NOT_CONFIGURED")

    prices: dict[uuid.UUID, Price] = {}
    for pid in product_ids:
        price = await _active_price(db, pid)
        if price is None:
            raise Conflict("产品缺少生效价格", code="PRICE_NOT_CONFIGURED")
        prices[pid] = price

    currency = prices[product_ids[0]].currency
    if any(prices[pid].currency != currency for pid in product_ids):
        raise InvalidInput("订单内产品币种不一致", details={"currency": currency})
    total = sum((prices[pid].amount for pid in product_ids), Decimal(0))

    order = Order(
        user_id=user.id,
        status=OrderStatus.PENDING.value,
        currency=currency,
        total_amount=total,
        idempotency_key=key,
        expires_at=_now() + timedelta(minutes=DEFAULT_ORDER_TTL_MINUTES),
        refund_capability_version=_refund_capability_version(),
    )
    db.add(order)
    await db.flush()

    for pid in product_ids:
        product = by_id[pid]
        price = prices[pid]
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=pid,
                price_id=price.id,
                title=product.title,
                quantity=1,
                unit_amount=price.amount,
                amount=price.amount,
                currency=currency,
                grant_scope_type=product.grant_scope_type,
                grant_scope_id=product.grant_scope_id,
                grant_capability=product.grant_capability,
                grant_duration_days=product.grant_duration_days,
                # 下单时冻结的销售主体、价格快照与退款能力版本（§15.4）；
                # 比例等收益规则由服务端在确认到账时按冻结数据生成，不由前端传入。
                seller_party_id=product.owner_id,
                price_snapshot={
                    "price_id": str(price.id),
                    "version": price.version,
                    "amount": str(price.amount),
                    "currency": currency,
                },
                refund_capability_version=_refund_capability_version(),
            )
        )
    await db.flush()

    if payload.channel:
        channel = channels.get_channel(payload.channel)
        # 未配置渠道时抛 NotEnabled，整单不落库（不假装下单成功）
        payment = await channel.create_payment(
            order_ref=str(order.id),
            amount=total,
            currency=currency,
            subject=f"订单 {order.id}",
        )
        db.add(
            PaymentAttempt(
                order_id=order.id,
                channel=payload.channel,
                status=PaymentAttemptStatus.CREATED.value,
                amount=total,
                currency=currency,
                channel_txn_id=payment.channel_txn_id,
                channel_reference=payment.channel_reference,
            )
        )
        await db.flush()

    await record_audit(
        db,
        action="order.create",
        object_type="order",
        object_id=order.id,
        actor_id=user.id,
        after={
            "user_id": str(user.id),
            "currency": currency,
            "total_amount": str(total),
            "product_ids": [str(pid) for pid in product_ids],
        },
        request_id=request_id,
    )
    await idem.remember(
        db,
        scope=scope,
        key=key,
        payload=req_payload,
        actor_id=user.id,
        response_status=201,
        response={"order_id": str(order.id)},
    )
    return await _order_out(db, order)


async def get_order(db: AsyncSession, *, user: "User", order_id: uuid.UUID) -> OrderOut:
    """订单详情；只读。非本人且非管理员返回 404，避免泄露存在性（§25.1）。"""
    order = await _get_order(db, order_id)
    if order.user_id != user.id and user.role != Role.ADMIN:
        raise NotFound("订单不存在")
    return await _order_out(db, order)


async def cancel_order(
    db: AsyncSession, *, user: "User", order_id: uuid.UUID, reason: str | None, request_id: str
) -> OrderOut:
    """取消待支付订单；仅本人或管理员，非待支付状态抛冲突（§29）。"""
    _require_enabled()
    order = await _get_order(db, order_id)
    if order.user_id != user.id and user.role != Role.ADMIN:
        raise NotFound("订单不存在")
    if order.status == OrderStatus.CANCELLED.value:
        return await _order_out(db, order)
    if order.status != OrderStatus.PENDING.value:
        raise Conflict("当前订单状态不可取消", code="ORDER_NOT_CANCELLABLE")

    before = {"status": order.status}
    order.status = OrderStatus.CANCELLED.value
    order.cancelled_at = _now()
    attempts = list(
        (
            await db.execute(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id))
        )
        .scalars()
        .all()
    )
    for attempt in attempts:
        if attempt.status == PaymentAttemptStatus.CREATED.value:
            attempt.status = PaymentAttemptStatus.CLOSED.value
    await db.flush()
    await record_audit(
        db,
        action="order.cancel",
        object_type="order",
        object_id=order.id,
        actor_id=user.id,
        before=before,
        after={"status": order.status},
        reason=reason,
        request_id=request_id,
    )
    return await _order_out(db, order)


# --------------------------------------------------------------------------
# 支付与回调（§24.3）
# --------------------------------------------------------------------------
async def confirm_payment(
    db: AsyncSession, *, channel: str, payload: dict[str, Any]
) -> PaymentAttempt:
    """确认渠道回调（docs/INTERFACES.md `commerce.confirm_payment`）。

    `payload` 必须由渠道适配器 `verify_callback` 产出并带 `signature_verified=True`；
    校验订单、金额、币种与渠道交易 ID；重复回调不重复授予权益。
    """
    _require_enabled()
    if payload.get("signature_verified") is not True:
        raise Forbidden("回调未通过验签", code="CALLBACK_NOT_VERIFIED")

    order_ref = payload.get("order_ref")
    channel_txn_id = payload.get("channel_txn_id")
    status = str(payload.get("status") or "")
    amount = _to_decimal(payload.get("amount"))
    currency = payload.get("currency")
    if not order_ref or not channel_txn_id:
        raise InvalidInput("回调缺少订单引用或渠道交易 ID")

    order_uuid = parse_uuid(str(order_ref), "order_ref")
    order = await _get_order(db, order_uuid)

    existing = (
        await db.execute(
            select(PaymentAttempt).where(
                PaymentAttempt.channel == channel,
                PaymentAttempt.channel_txn_id == str(channel_txn_id),
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == PaymentAttemptStatus.SUCCEEDED.value:
        # 重复回调：不重复授予权益（§24.3）
        return existing
    if existing is not None and existing.order_id != order.id:
        raise Conflict("渠道交易 ID 已绑定其他订单", code="CHANNEL_TXN_CONFLICT")

    if amount is None or amount != Decimal(str(order.total_amount)):
        raise Conflict(
            "回调金额与订单不一致",
            code="AMOUNT_MISMATCH",
            details={"expected": str(order.total_amount), "actual": payload.get("amount")},
        )
    if currency is not None and str(currency) != order.currency:
        raise Conflict(
            "回调币种与订单不一致",
            code="CURRENCY_MISMATCH",
            details={"expected": order.currency, "actual": currency},
        )

    if existing is None:
        existing = PaymentAttempt(
            order_id=order.id,
            channel=channel,
            status=PaymentAttemptStatus.CREATED.value,
            amount=order.total_amount,
            currency=order.currency,
            channel_txn_id=str(channel_txn_id),
        )
        db.add(existing)
        try:
            await db.flush()
        except IntegrityError:
            # 并发回调：另一事务已写入同一渠道交易 ID，回滚后按其结果处理
            await db.rollback()
            order = await _get_order(db, order_uuid)
            existing = (
                await db.execute(
                    select(PaymentAttempt).where(
                        PaymentAttempt.channel == channel,
                        PaymentAttempt.channel_txn_id == str(channel_txn_id),
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise Conflict("支付回调并发冲突，请重试", code="PAYMENT_CONFLICT")
            if existing.status == PaymentAttemptStatus.SUCCEEDED.value:
                return existing

    if status == "failed":
        existing.status = PaymentAttemptStatus.FAILED.value
        await db.flush()
        await record_audit(
            db,
            action="payment.failed",
            object_type="payment_attempt",
            object_id=existing.id,
            actor_id=None,
            after={"status": existing.status, "channel": channel},
            request_id=None,
        )
        return existing
    if status != "succeeded":
        # 渠道已受理但未完成：本地待确认的可恢复状态（§24.3）
        existing.status = PaymentAttemptStatus.PENDING_CONFIRMATION.value
        await db.flush()
        return existing

    existing.status = PaymentAttemptStatus.SUCCEEDED.value
    existing.confirmed_at = _now()
    granted = await _grant_order_entitlements(db, order)
    # beta15（§9.2）：按冻结组成生成明细级分配与初始计提，与确认、授权同事务提交。
    await _generate_order_allocations(db, order)
    if order.status != OrderStatus.PAID.value:
        order.status = OrderStatus.PAID.value
        order.paid_at = _now()
    await _post_balanced(
        db,
        currency=order.currency,
        business_type="order_paid",
        business_id=order.id,
        entries=[
            _Entry(
                LedgerAccount.PLATFORM.value,
                None,
                LedgerDirection.DEBIT.value,
                Decimal(str(order.total_amount)),
                "收到用户支付",
                f"order_paid:{order.id}:debit",
            ),
            _Entry(
                LedgerAccount.PLATFORM.value,
                None,
                LedgerDirection.CREDIT.value,
                Decimal(str(order.total_amount)),
                "未结算收款（账目细分待 D16 确认）",
                f"order_paid:{order.id}:credit",
            ),
        ],
    )
    await emit_event(
        db,
        event_type=DomainEventType.ORDER_PAID,
        subject_type="order",
        subject_id=order.id,
        payload={
            "user_id": str(order.user_id),
            "currency": order.currency,
            "total_amount": str(order.total_amount),
            "granted_entitlements": granted,
        },
        dedupe_key=f"order.paid:{order.id}",
    )
    await record_audit(
        db,
        action="payment.confirmed",
        object_type="payment_attempt",
        object_id=existing.id,
        actor_id=None,
        after={
            "order_id": str(order.id),
            "status": existing.status,
            "channel": channel,
            "channel_txn_id": str(channel_txn_id),
            "granted_entitlements": granted,
        },
        request_id=None,
    )
    await db.flush()
    return existing


async def entitlement_grants(
    db: AsyncSession, *, user_id: uuid.UUID, scope_type: Any, scope_id: uuid.UUID
) -> list["Entitlement"]:
    """供 access 模块判定权益时调用（docs/INTERFACES.md）。

    未启用时返回空列表，不伪造权益。
    """
    if not settings.COMMERCE_ENABLED:
        return []

    from app.modules.access.models import Entitlement

    stmt = select(Entitlement).where(
        Entitlement.user_id == user_id,
        Entitlement.scope_id == scope_id,
        Entitlement.source_type == EntitlementSource.ORDER.value,
        Entitlement.scope_type == str(scope_type),
        Entitlement.revoked_at.is_(None),
    )
    return list((await db.execute(stmt)).scalars().all())


async def _grant_order_entitlements(db: AsyncSession, order: Order) -> int:
    """按订单行授予权益；同一订单重复调用不重复授予。"""
    from app.modules.access.models import Entitlement

    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
    )
    existing = {
        (row.scope_type, row.scope_id, row.capability)
        for row in (
            await db.execute(
                select(Entitlement).where(
                    Entitlement.user_id == order.user_id,
                    Entitlement.source_type == EntitlementSource.ORDER.value,
                    Entitlement.source_id == order.id,
                    Entitlement.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    created = 0
    for item in items:
        key = (item.grant_scope_type, item.grant_scope_id, item.grant_capability)
        if key in existing:
            continue
        expires_at = (
            _now() + timedelta(days=item.grant_duration_days)
            if item.grant_duration_days
            else None
        )
        db.add(
            Entitlement(
                user_id=order.user_id,
                scope_type=item.grant_scope_type,
                scope_id=item.grant_scope_id,
                capability=item.grant_capability,
                source_type=EntitlementSource.ORDER.value,
                source_id=order.id,
                starts_at=_now(),
                expires_at=expires_at,
            )
        )
        existing.add(key)
        created += 1
    if created:
        await db.flush()
    return created


async def _generate_order_allocations(db: AsyncSession, order: Order) -> int:
    """按订单行的冻结组成生成明细级分配与初始计提（§9.2）。

    无 `composition_snapshot` 的行（旧订单）不生成分配，保持原语义（§18.2）。
    返回生成的组件分配数。
    """
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
    )
    allocated = 0
    for item in items:
        specs = _component_specs_from_snapshot(item.composition_snapshot)
        if not specs:
            continue
        allocated += len(await allocate_order_item(db, item, specs))
    if allocated:
        await accrue_revenue(db, order)
    return allocated


# --------------------------------------------------------------------------
# 退款（§24.3）
# --------------------------------------------------------------------------
async def create_refund(
    db: AsyncSession,
    *,
    user: "User",
    payload: RefundCreate,
    request_id: str,
    idempotency_key: str | None,
) -> RefundOut:
    """发起退款：未确认前记录处理中；渠道未配置时整单失败，不假成功。"""
    _require_enabled()
    if payload.scope == REFUND_SCOPE_ORDER_ITEMS:
        # 明细退款能力未启用：显式拒绝，绝不静默退整单（§16.3、§18.3）。
        raise Conflict(
            "明细退款能力未启用",
            code="REFUND_SCOPE_UNSUPPORTED",
            details={
                "scope": REFUND_SCOPE_ORDER_ITEMS,
                "reason": "order_items_refund_not_enabled",
                "gate_closed": settings.COMMERCE_COMPLIANCE_GATE_CLOSED,
            },
        )
    key = idem.normalize_key(idempotency_key)
    scope = f"{REFUND_SCOPE}:{user.id}"
    req_payload = {
        "order_id": str(payload.order_id),
        "amount": str(payload.amount) if payload.amount is not None else None,
        "reason": payload.reason,
        "scope": payload.scope,
    }
    cached = await idem.lookup(db, scope=scope, key=key, payload=req_payload, actor_id=user.id)
    if cached is not None:
        return await _refund_out(db, await _get_refund(db, uuid.UUID(cached["refund_id"])))

    order = await _get_order(db, payload.order_id)
    if order.user_id != user.id:
        raise NotFound("订单不存在")
    if order.status not in (OrderStatus.PAID.value, OrderStatus.REFUNDING.value):
        raise Conflict("当前订单状态不可退款", code="ORDER_NOT_REFUNDABLE")

    already = await _refund_total(
        db,
        order.id,
        statuses=(
            RefundStatus.REQUESTED.value,
            RefundStatus.PROCESSING.value,
            RefundStatus.SUCCEEDED.value,
        ),
    )
    refundable = Decimal(str(order.total_amount)) - already
    amount = payload.amount if payload.amount is not None else refundable
    if amount <= 0 or amount > refundable:
        raise Conflict(
            "退款金额超过可退金额",
            code="REFUND_EXCEEDS_REFUNDABLE",
            details={"refundable": str(refundable)},
        )

    attempt = (
        await db.execute(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.order_id == order.id,
                PaymentAttempt.status == PaymentAttemptStatus.SUCCEEDED.value,
            )
            .order_by(PaymentAttempt.created_at.desc())
        )
    ).scalars().first()
    if attempt is None or not attempt.channel_txn_id:
        raise Conflict("订单没有可退款的渠道交易", code="NO_REFUNDABLE_PAYMENT")

    refund = Refund(
        order_id=order.id,
        payment_attempt_id=attempt.id,
        requested_by=user.id,
        status=RefundStatus.REQUESTED.value,
        amount=amount,
        currency=order.currency,
        reason=payload.reason,
        idempotency_key=key,
    )
    db.add(refund)
    await db.flush()

    channel = channels.get_channel(attempt.channel)
    # 未配置渠道时抛 NotEnabled，整个退款申请回滚
    result = await channel.create_refund(
        channel_txn_id=attempt.channel_txn_id,
        amount=amount,
        currency=order.currency,
        reason=payload.reason,
    )
    refund.status = RefundStatus.PROCESSING.value
    refund.channel_refund_id = result.channel_refund_id
    order.status = OrderStatus.REFUNDING.value
    await db.flush()

    await record_audit(
        db,
        action="refund.request",
        object_type="refund",
        object_id=refund.id,
        actor_id=user.id,
        after={
            "order_id": str(order.id),
            "amount": str(amount),
            "currency": order.currency,
            "status": refund.status,
        },
        reason=payload.reason,
        request_id=request_id,
    )
    await idem.remember(
        db,
        scope=scope,
        key=key,
        payload=req_payload,
        actor_id=user.id,
        response_status=201,
        response={"refund_id": str(refund.id)},
    )
    return await _refund_out(db, refund)


async def list_refunds(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    order_id: uuid.UUID | None = None,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[RefundOut]:
    """退款列表；只读。传入 user_id 时只看本人，管理员可不传。"""
    size = normalize_page_size(limit)
    stmt = select(Refund).order_by(Refund.created_at.desc(), Refund.id.desc())
    if user_id is not None:
        stmt = stmt.where(Refund.requested_by == user_id)
    if order_id is not None:
        stmt = stmt.where(Refund.order_id == order_id)
    stmt = _apply_time_cursor(stmt, Refund, cursor)
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = _cursor_of(items[-1]) if has_more and items else None
    return CursorPage[RefundOut](
        items=[RefundOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def confirm_refund(
    db: AsyncSession,
    *,
    actor: "User",
    refund_id: uuid.UUID,
    payload: RefundConfirmIn,
    request_id: str,
) -> RefundOut:
    """确认退款：撤销来自该订单的权益（不误撤其他来源），并用反向分录更正账本。"""
    _require_enabled()
    refund = await _get_refund(db, refund_id)
    if refund.status == RefundStatus.SUCCEEDED.value:
        return await _refund_out(db, refund)
    if refund.status not in (RefundStatus.REQUESTED.value, RefundStatus.PROCESSING.value):
        raise Conflict("当前退款状态不可确认", code="REFUND_NOT_CONFIRMABLE")

    before = {"status": refund.status}
    refund.status = RefundStatus.SUCCEEDED.value
    refund.confirmed_by = actor.id
    refund.confirmed_at = _now()
    if payload.channel_refund_id:
        refund.channel_refund_id = payload.channel_refund_id
    await db.flush()

    order = await _get_order(db, refund.order_id)
    revoked = await _revoke_order_entitlements(db, order)

    originals = list(
        (
            await db.execute(
                select(LedgerEntry).where(
                    LedgerEntry.business_type == "order_paid",
                    LedgerEntry.business_id == order.id,
                )
            )
        )
        .scalars()
        .all()
    )
    await _reverse_entries(
        db,
        originals=originals,
        business_type="refund_reversal",
        business_id=refund.id,
        reason="退款确认冲正",
    )

    succeeded_total = await _refund_total(
        db, order.id, statuses=(RefundStatus.SUCCEEDED.value,)
    )
    is_full_refund = succeeded_total >= Decimal(str(order.total_amount))
    order.status = (
        OrderStatus.REFUNDED.value if is_full_refund else OrderStatus.REFUNDING.value
    )
    # 累计成功退款（分）与明细级收益冲回（§11.6、§14.4 不变量 4）。
    order.refunded_fen = (order.refunded_fen or 0) + _fen(refund.amount)
    if is_full_refund:
        # 首期兼容策略：整单退全额时按明细分项冲回各受益人收益（§11.2、§18.2）。
        await reverse_order_revenue(db, refund)
    await db.flush()

    await emit_event(
        db,
        event_type=DomainEventType.REFUND_CONFIRMED,
        subject_type="refund",
        subject_id=refund.id,
        payload={
            "order_id": str(order.id),
            "user_id": str(order.user_id),
            "amount": str(refund.amount),
            "revoked_entitlements": revoked,
        },
        dedupe_key=f"refund.confirmed:{refund.id}",
    )
    await record_audit(
        db,
        action="refund.confirm",
        object_type="refund",
        object_id=refund.id,
        actor_id=actor.id,
        before=before,
        after={
            "status": refund.status,
            "order_id": str(order.id),
            "revoked_entitlements": revoked,
        },
        reason=payload.reason,
        request_id=request_id,
    )
    return await _refund_out(db, refund)


async def _revoke_order_entitlements(db: AsyncSession, order: Order) -> int:
    """只撤销来源为该订单的权益，避免误撤免费开放、作者、管理员等其他来源（§24.3）。"""
    from app.modules.access.models import Entitlement

    rows = list(
        (
            await db.execute(
                select(Entitlement).where(
                    Entitlement.user_id == order.user_id,
                    Entitlement.source_type == EntitlementSource.ORDER.value,
                    Entitlement.source_id == order.id,
                    Entitlement.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = _now()
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = "退款确认"
    if rows:
        await db.flush()
    return len(rows)


async def _refund_total(
    db: AsyncSession, order_id: uuid.UUID, *, statuses: tuple[str, ...]
) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.order_id == order_id, Refund.status.in_(statuses)
        )
    )
    return Decimal(str(total or 0))


# --------------------------------------------------------------------------
# 对账（§24.3，只读）
# --------------------------------------------------------------------------
def _expected_channel_status(local_status: str) -> str:
    return {
        PaymentAttemptStatus.SUCCEEDED.value: "succeeded",
        PaymentAttemptStatus.FAILED.value: "failed",
        PaymentAttemptStatus.CLOSED.value: "closed",
    }.get(local_status, "pending")


async def list_reconciliations(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[ReconciliationItem]:
    """本地支付尝试与渠道状态的差异视图；渠道未配置时只展示本地事实，不伪造。"""
    size = normalize_page_size(limit)
    stmt = select(PaymentAttempt).order_by(
        PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc()
    )
    stmt = _apply_time_cursor(stmt, PaymentAttempt, cursor)
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items_rows = rows[:size]

    items: list[ReconciliationItem] = []
    for attempt in items_rows:
        channel_status = "unknown"
        channel_amount: Decimal | None = None
        channel_currency: str | None = None
        matches = False
        note: str | None = None
        channel = channels.get_channel(attempt.channel)
        try:
            result = await channel.query_payment(channel_txn_id=attempt.channel_txn_id or "")
            channel_status = result.status
            channel_amount = result.amount
            channel_currency = result.currency
            matches = (
                result.status == _expected_channel_status(attempt.status)
                and (result.amount is None or result.amount == Decimal(str(attempt.amount)))
                and (result.currency is None or result.currency == attempt.currency)
            )
        except NotEnabled:
            channel_status = "channel_not_configured"
            note = "渠道未配置，仅展示本地状态"
        items.append(
            ReconciliationItem(
                payment_attempt_id=attempt.id,
                order_id=attempt.order_id,
                channel=attempt.channel,
                local_status=attempt.status,
                channel_status=channel_status,
                amount=attempt.amount,
                currency=attempt.currency,
                channel_amount=channel_amount,
                channel_currency=channel_currency,
                channel_txn_id=attempt.channel_txn_id,
                matches=matches,
                note=note,
            )
        )

    next_cursor = _cursor_of(items_rows[-1]) if has_more and items_rows else None
    return CursorPage[ReconciliationItem](
        items=items, next_cursor=next_cursor, has_more=has_more
    )


# --------------------------------------------------------------------------
# 账本查询（§24.3，只读；供 creator 的 GET /me/ledger 使用）
# --------------------------------------------------------------------------
async def list_ledger_entries(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[LedgerEntryOut]:
    size = normalize_page_size(limit)
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.account_id == user_id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
    )
    stmt = _apply_time_cursor(stmt, LedgerEntry, cursor)
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = _cursor_of(items[-1]) if has_more and items else None
    return CursorPage[LedgerEntryOut](
        items=[LedgerEntryOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------
# 提现（§24.3；供 creator 的 /me/withdrawals 使用）
# --------------------------------------------------------------------------
async def create_withdrawal(
    db: AsyncSession,
    *,
    user: "User",
    payload: WithdrawalCreate,
    request_id: str,
    idempotency_key: str | None,
) -> WithdrawalOut:
    """申请提现：最低提现额未配置时抛 NotEnabled；余额从账本核验（§24.3）。"""
    _require_enabled()
    key = idem.normalize_key(idempotency_key)
    scope = f"{WITHDRAWAL_SCOPE}:{user.id}"
    req_payload = {"amount": str(payload.amount), "currency": payload.currency}
    cached = await idem.lookup(db, scope=scope, key=key, payload=req_payload, actor_id=user.id)
    if cached is not None:
        return await _withdrawal_out(db, uuid.UUID(cached["withdrawal_id"]))

    minimum = min_withdrawal_amount(payload.currency)
    if minimum is None:
        raise NotEnabled(
            "最低提现额尚未配置", details={"reason": "min_withdrawal_not_configured"}
        )
    if payload.amount < minimum:
        raise InvalidInput(
            "提现金额低于最低提现额", details={"min_withdrawal": str(minimum)}
        )

    balance = await _ledger_balance(
        db,
        account=LedgerAccount.CREATOR.value,
        account_id=user.id,
        currency=payload.currency,
    )
    if payload.amount > balance:
        raise Conflict(
            "可提现余额不足",
            code="INSUFFICIENT_BALANCE",
            details={"available": str(balance)},
        )

    withdrawal = Withdrawal(
        creator_id=user.id,
        status=WithdrawalStatus.REQUESTED.value,
        amount=payload.amount,
        currency=payload.currency,
        idempotency_key=key,
    )
    db.add(withdrawal)
    await db.flush()

    # 冻结：借方创作者账户、贷方平台账户（待提现与已提现分开，§24.3）
    await _post_balanced(
        db,
        currency=payload.currency,
        business_type="withdrawal_hold",
        business_id=withdrawal.id,
        entries=[
            _Entry(
                LedgerAccount.CREATOR.value,
                user.id,
                LedgerDirection.DEBIT.value,
                payload.amount,
                "提现冻结",
                f"withdrawal_hold:{withdrawal.id}:debit",
            ),
            _Entry(
                LedgerAccount.PLATFORM.value,
                None,
                LedgerDirection.CREDIT.value,
                payload.amount,
                "提现冻结",
                f"withdrawal_hold:{withdrawal.id}:credit",
            ),
        ],
    )
    await record_audit(
        db,
        action="withdrawal.request",
        object_type="withdrawal",
        object_id=withdrawal.id,
        actor_id=user.id,
        after={"amount": str(payload.amount), "currency": payload.currency},
        request_id=request_id,
    )
    await idem.remember(
        db,
        scope=scope,
        key=key,
        payload=req_payload,
        actor_id=user.id,
        response_status=201,
        response={"withdrawal_id": str(withdrawal.id)},
    )
    return WithdrawalOut.model_validate(withdrawal)


async def list_withdrawals(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[WithdrawalOut]:
    size = normalize_page_size(limit)
    stmt = (
        select(Withdrawal)
        .where(Withdrawal.creator_id == user_id)
        .order_by(Withdrawal.created_at.desc(), Withdrawal.id.desc())
    )
    stmt = _apply_time_cursor(stmt, Withdrawal, cursor)
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = _cursor_of(items[-1]) if has_more and items else None
    return CursorPage[WithdrawalOut](
        items=[WithdrawalOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------
# 结算（§24.3；供 creator 的 POST /admin/settlements/run 使用）
# --------------------------------------------------------------------------
async def run_settlement(
    db: AsyncSession, *, actor: "User", payload: SettlementRunIn, request_id: str
) -> SettlementRunOut:
    """按已批准规则计算某周期结算；比例未配置时抛 NotEnabled（D16）。"""
    _require_enabled()
    period_start = _aware(payload.period_start)
    period_end = _aware(payload.period_end)
    if period_end <= period_start:
        raise InvalidInput("结算周期无效")
    ratio = creator_pool_ratio()
    if ratio is None:
        raise NotEnabled(
            "创作者分成比例尚未配置", details={"reason": "creator_pool_ratio_not_configured"}
        )
    rule_ref = f"creator_pool_ratio={ratio}"

    rows = (
        await db.execute(
            select(Product.owner_id, func.coalesce(func.sum(OrderItem.amount), 0))
            .join(OrderItem, OrderItem.product_id == Product.id)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.status == OrderStatus.PAID.value,
                Order.currency == payload.currency,
                Order.paid_at.is_not(None),
                Order.paid_at >= period_start,
                Order.paid_at < period_end,
                Product.owner_id.is_not(None),
            )
            .group_by(Product.owner_id)
        )
    ).all()

    created_any = False
    settlements: list[Settlement] = []
    for owner_id, gross_raw in rows:
        gross = Decimal(str(gross_raw or 0))
        existing = (
            await db.execute(
                select(Settlement).where(
                    Settlement.creator_id == owner_id,
                    Settlement.period_start == period_start,
                    Settlement.period_end == period_end,
                    Settlement.currency == payload.currency,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 同一周期结算幂等（§24.3）
            settlements.append(existing)
            continue

        net = (gross * ratio).quantize(MONEY_QUANT)
        fee = gross - net
        settlement = Settlement(
            creator_id=owner_id,
            period_start=period_start,
            period_end=period_end,
            currency=payload.currency,
            status=SettlementStatus.WITHDRAWABLE.value,
            gross_amount=gross,
            platform_fee=fee,
            net_amount=net,
            rule_ref=rule_ref,
        )
        db.add(settlement)
        await db.flush()

        # 借方平台毛额；贷方创作者净额、贷方平台分成，借贷平衡
        await _post_balanced(
            db,
            currency=payload.currency,
            business_type="settlement",
            business_id=settlement.id,
            settlement_id=settlement.id,
            entries=[
                _Entry(
                    LedgerAccount.PLATFORM.value,
                    None,
                    LedgerDirection.DEBIT.value,
                    gross,
                    "周期结算",
                    f"settlement:{settlement.id}:debit",
                ),
                _Entry(
                    LedgerAccount.CREATOR.value,
                    owner_id,
                    LedgerDirection.CREDIT.value,
                    net,
                    "创作者收益",
                    f"settlement:{settlement.id}:creator",
                ),
                _Entry(
                    LedgerAccount.PLATFORM.value,
                    None,
                    LedgerDirection.CREDIT.value,
                    fee,
                    "平台分成",
                    f"settlement:{settlement.id}:platform_fee",
                ),
            ],
        )
        await record_audit(
            db,
            action="settlement.run",
            object_type="settlement",
            object_id=settlement.id,
            actor_id=actor.id,
            after={
                "creator_id": str(owner_id),
                "currency": payload.currency,
                "gross_amount": str(gross),
                "net_amount": str(net),
                "rule_ref": rule_ref,
            },
            request_id=request_id,
        )
        created_any = True
        settlements.append(settlement)

    return SettlementRunOut(
        created=created_any,
        settlements=[SettlementOut.model_validate(item) for item in settlements],
    )


# --------------------------------------------------------------------------
# beta15 收益子账（§12.1、§14.3、§15.5）
# --------------------------------------------------------------------------
async def _ensure_account(
    db: AsyncSession, party_id: uuid.UUID | None, currency: str, bucket: str
) -> LedgerAccountRecord:
    """取得（或创建）主体＋币种＋分桶账户；平台主体为 NULL。"""
    stmt = select(LedgerAccountRecord).where(
        LedgerAccountRecord.currency == currency, LedgerAccountRecord.bucket == bucket
    )
    if party_id is None:
        stmt = stmt.where(LedgerAccountRecord.party_id.is_(None))
    else:
        stmt = stmt.where(LedgerAccountRecord.party_id == party_id)
    account = (await db.execute(stmt)).scalars().first()
    if account is None:
        account = LedgerAccountRecord(party_id=party_id, currency=currency, bucket=bucket)
        db.add(account)
        await db.flush()
    return account


async def _write_journal_lines(
    db: AsyncSession,
    journal: LedgerJournal,
    lines: list[tuple[LedgerAccountRecord, int, int, uuid.UUID | None]],
) -> None:
    """写入一组分录行；借方合计必须等于贷方合计（§15.5）。"""
    debits = sum(debit for _, debit, _, _ in lines)
    credits = sum(credit for _, _, credit, _ in lines)
    if debits != credits:
        raise Conflict(
            "账本分录不平衡",
            code="LEDGER_UNBALANCED",
            details={"debit_fen": debits, "credit_fen": credits},
        )
    for account, debit, credit, allocation_id in lines:
        if debit == 0 and credit == 0:
            continue
        db.add(
            LedgerLine(
                journal_id=journal.id,
                account_id=account.id,
                allocation_id=allocation_id,
                debit_fen=debit,
                credit_fen=credit,
            )
        )
    await db.flush()


async def _bucket_balance(
    db: AsyncSession, party_id: uuid.UUID | None, currency: str, bucket: str
) -> int:
    """分桶余额 = 贷方合计 − 借方合计（整数分）。"""
    stmt = select(LedgerAccountRecord).where(
        LedgerAccountRecord.currency == currency, LedgerAccountRecord.bucket == bucket
    )
    if party_id is None:
        stmt = stmt.where(LedgerAccountRecord.party_id.is_(None))
    else:
        stmt = stmt.where(LedgerAccountRecord.party_id == party_id)
    account = (await db.execute(stmt)).scalars().first()
    if account is None:
        return 0
    credits = await db.scalar(
        select(func.coalesce(func.sum(LedgerLine.credit_fen), 0)).where(
            LedgerLine.account_id == account.id
        )
    )
    debits = await db.scalar(
        select(func.coalesce(func.sum(LedgerLine.debit_fen), 0)).where(
            LedgerLine.account_id == account.id
        )
    )
    return int(credits or 0) - int(debits or 0)


# --------------------------------------------------------------------------
# beta15 明细级收益分配（§10、§14.4、§19 T15）
# --------------------------------------------------------------------------
async def allocate_order_item(
    db: AsyncSession, order_item: OrderItem, components: list[ComponentSpec]
) -> list[OrderItemComponentAllocation]:
    """按组件与受益人写 `order_item_component_allocations` + `order_item_allocations`（§10.1）。

    金额守恒：明细实收 = 组件分配合计；组件 `base − deduction = creator_total + platform`；
    各受益人 `creator_fen` 合计 = 组件 `creator_total_fen`。
    分尾差用最大余数法分配（§10.2），重跑顺序与结果一致：同一明细已分配时原样返回。
    """
    _require_enabled()
    existing = list(
        (
            await db.execute(
                select(OrderItemComponentAllocation)
                .where(OrderItemComponentAllocation.order_item_id == order_item.id)
                .order_by(OrderItemComponentAllocation.component_key)
            )
        )
        .scalars()
        .all()
    )
    if existing:
        return existing

    specs = list(components)
    if not specs:
        raise InvalidInput("订单明细缺少组件分配", details={"order_item_id": str(order_item.id)})
    keys = [spec.component_key for spec in specs]
    if len(set(keys)) != len(keys):
        raise InvalidInput("组件 key 重复", details={"component_keys": keys})

    item_fen = _fen(order_item.amount)
    for spec in specs:
        if spec.weight < 0 or spec.deduction_fen < 0:
            raise InvalidInput("组件权重或扣减不能为负", details={"component_key": spec.component_key})
        if not 0 <= spec.creator_bps <= BPS_DENOMINATOR:
            raise InvalidInput("贡献者分成基点必须在 0—10000", details={"component_key": spec.component_key})

    allocated = _largest_remainder(item_fen, [(spec.component_key, spec.weight) for spec in specs])

    created: list[OrderItemComponentAllocation] = []
    for spec in sorted(specs, key=lambda s: s.component_key):
        component_fen = allocated[spec.component_key]
        base_fen = component_fen - spec.deduction_fen
        if base_fen < 0:
            raise Conflict(
                "组件扣减超过分配金额", code="DEDUCTION_EXCEEDS_ALLOCATION",
                details={"component_key": spec.component_key},
            )
        creator_total = base_fen * spec.creator_bps // BPS_DENOMINATOR
        platform_fen = base_fen - creator_total
        component = OrderItemComponentAllocation(
            order_item_id=order_item.id,
            component_key=spec.component_key,
            allocated_fen=component_fen,
            base_fen=base_fen,
            deduction_fen=spec.deduction_fen,
            creator_total_fen=creator_total,
            platform_fen=platform_fen,
            rule_snapshot={
                "weight": spec.weight,
                "creator_bps": spec.creator_bps,
                "deduction_fen": spec.deduction_fen,
                "rounding": "largest_remainder",
            },
        )
        db.add(component)
        await db.flush()

        shares = list(spec.beneficiaries)
        if creator_total > 0 and not shares:
            raise Conflict(
                "组件有贡献者收益但未指定受益人", code="BENEFICIARY_REQUIRED",
                details={"component_key": spec.component_key},
            )
        if shares and sum(share.share_bps for share in shares) != BPS_DENOMINATOR:
            raise InvalidInput("受益人份额合计必须为 10000", details={"component_key": spec.component_key})
        split = (
            _largest_remainder(
                creator_total,
                [(str(share.beneficiary_party_id), share.share_bps) for share in shares],
            )
            if shares
            else {}
        )
        for share in sorted(shares, key=lambda s: str(s.beneficiary_party_id)):
            db.add(
                OrderItemAllocation(
                    component_allocation_id=component.id,
                    beneficiary_party_id=share.beneficiary_party_id,
                    agreement_version_id=spec.agreement_version_id,
                    creator_fen=split.get(str(share.beneficiary_party_id), 0),
                    share_snapshot={"share_bps": share.share_bps},
                )
            )
        await db.flush()
        created.append(component)

    if sum(component.allocated_fen for component in created) != item_fen:
        raise Conflict("组件分配金额与明细金额不一致", code="ALLOCATION_NOT_CONSERVED")
    return created


async def list_order_item_allocations(
    db: AsyncSession, order_item_id: uuid.UUID
) -> list[ComponentAllocationOut]:
    """管理员查询：某订单明细的组件分配与受益人拆分（只读，需 ledger.view）。"""
    components = list(
        (
            await db.execute(
                select(OrderItemComponentAllocation)
                .where(OrderItemComponentAllocation.order_item_id == order_item_id)
                .order_by(OrderItemComponentAllocation.component_key)
            )
        )
        .scalars()
        .all()
    )
    out: list[ComponentAllocationOut] = []
    for component in components:
        allocations = list(
            (
                await db.execute(
                    select(OrderItemAllocation)
                    .where(OrderItemAllocation.component_allocation_id == component.id)
                    .order_by(OrderItemAllocation.beneficiary_party_id)
                )
            )
            .scalars()
            .all()
        )
        dto = ComponentAllocationOut.model_validate(component)
        out.append(
            dto.model_copy(
                update={
                    "allocations": [
                        BeneficiaryAllocationOut.model_validate(item) for item in allocations
                    ]
                }
            )
        )
    return out


async def accrue_revenue(
    db: AsyncSession, order: Order, *, occurred_at: datetime | None = None
) -> LedgerJournal | None:
    """到账确认的初始计提（§12.1、§14.4 不变量 3）。

    写一个 journal 与一组借贷相等的 lines，并确保 `ledger_accounts` 分桶存在：
    借方平台账户（订单实收）；贷方各贡献者待成熟账户、平台归属与约定扣减。
    `event_key = order_accrual:{order.id}` 全局唯一，重复提交／超时重试只计提一次。
    旧订单无冻结组成时不生成收益，返回 None（§18.2）。
    """
    _require_enabled()
    event_key = f"order_accrual:{order.id}"
    existing = (
        await db.execute(select(LedgerJournal).where(LedgerJournal.event_key == event_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    components = list(
        (
            await db.execute(
                select(OrderItemComponentAllocation)
                .join(OrderItem, OrderItem.id == OrderItemComponentAllocation.order_item_id)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItemComponentAllocation.id)
            )
        )
        .scalars()
        .all()
    )
    if not components:
        return None

    creator_by_party: dict[uuid.UUID, int] = {}
    for component in components:
        allocations = list(
            (
                await db.execute(
                    select(OrderItemAllocation).where(
                        OrderItemAllocation.component_allocation_id == component.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for allocation in allocations:
            creator_by_party[allocation.beneficiary_party_id] = (
                creator_by_party.get(allocation.beneficiary_party_id, 0) + allocation.creator_fen
            )

    platform_total = sum(component.platform_fen for component in components)
    deduction_total = sum(component.deduction_fen for component in components)
    order_fen = _fen(order.total_amount)
    credited = sum(creator_by_party.values()) + platform_total + deduction_total
    if credited != order_fen:
        raise Conflict(
            "收益计提与订单金额不一致",
            code="ACCRUAL_NOT_CONSERVED",
            details={"order_fen": order_fen, "credited_fen": credited},
        )

    journal = LedgerJournal(
        event_key=event_key,
        event_type=LedgerEventType.ORDER_ACCRUAL.value,
        source_id=order.id,
        occurred_at=occurred_at or _now(),
    )
    db.add(journal)
    await db.flush()

    platform_account = await _ensure_account(
        db, None, order.currency, LedgerBucket.AVAILABLE.value
    )
    lines: list[tuple[LedgerAccountRecord, int, int, uuid.UUID | None]] = [
        (platform_account, order_fen, 0, None)
    ]
    for party_id in sorted(creator_by_party, key=str):
        fen = creator_by_party[party_id]
        if fen <= 0:
            continue
        account = await _ensure_account(db, party_id, order.currency, LedgerBucket.PENDING.value)
        lines.append((account, 0, fen, None))
    if platform_total > 0:
        lines.append((platform_account, 0, platform_total, None))
    if deduction_total > 0:
        # 约定扣减暂计入平台账户，具体科目待财务确认（§10.2）。
        lines.append((platform_account, 0, deduction_total, None))
    await _write_journal_lines(db, journal, lines)
    return journal


# --------------------------------------------------------------------------
# beta15 退款冲回（§11.4、§14.4 不变量 4、§19 T16/T17）
# --------------------------------------------------------------------------
async def reverse_revenue_for_refund(
    db: AsyncSession, refund: Refund, items: list[RefundItem]
) -> list[RefundComponentAllocation]:
    """退款成功后的收益冲回（§11.4）：按累计值算差额，避免多次小额退款累积误差。

    - 每组件本次基数冲回 `d_c`：由该明细累计退款 `Q` 与原分配推导（全退时 `Q = 明细实收`）。
    - 每组件累计贡献者冲回 = `floor(C × R / B)`，`R = B` 时取 `C`（完全冲尽）。
    - 平台冲回 = `d_c − 本次贡献者冲回`，只计一次；各受益人按剩余份额用最大余数法拆分。
    - 同一 `(refund_item, component_allocation)` 已冲回时跳过，重试幂等。
    同时写 `event_key = refund_reversal:{refund.id}` 的账本事件（待成熟 → 已冲回）。
    """
    _require_enabled()
    created: list[RefundComponentAllocation] = []
    creator_reversal_by_party: dict[uuid.UUID, int] = {}
    platform_reversal_total = 0

    for item in items:
        order_item = (
            await db.execute(select(OrderItem).where(OrderItem.id == item.order_item_id))
        ).scalar_one_or_none()
        if order_item is None:
            raise NotFound("订单明细不存在")
        components = list(
            (
                await db.execute(
                    select(OrderItemComponentAllocation)
                    .where(OrderItemComponentAllocation.order_item_id == order_item.id)
                    .order_by(OrderItemComponentAllocation.component_key)
                )
            )
            .scalars()
            .all()
        )
        if not components:
            continue
        item_fen = _fen(order_item.amount)
        if item.succeeded_fen == 0:
            item.succeeded_fen = item.amount_fen
        # 会话 autoflush=False：先落盘本轮成功退款，累计退款查询才能看到本轮金额。
        await db.flush()
        cumulative_refund = int(
            await db.scalar(
                select(func.coalesce(func.sum(RefundItem.succeeded_fen), 0)).where(
                    RefundItem.order_item_id == order_item.id
                )
            )
            or 0
        )
        if item_fen > 0 and cumulative_refund > item_fen:
            raise Conflict(
                "累计退款超过明细实收",
                code="REFUND_EXCEEDS_ITEM",
                details={"order_item_id": str(order_item.id)},
            )

        for component in components:
            already = (
                await db.execute(
                    select(RefundComponentAllocation).where(
                        RefundComponentAllocation.refund_item_id == item.id,
                        RefundComponentAllocation.component_allocation_id == component.id,
                    )
                )
            ).scalar_one_or_none()
            if already is not None:
                continue

            previous_base = int(
                await db.scalar(
                    select(func.coalesce(func.sum(RefundComponentAllocation.base_reduction_fen), 0)).where(
                        RefundComponentAllocation.component_allocation_id == component.id
                    )
                )
                or 0
            )
            previous_creator = int(
                await db.scalar(
                    select(
                        func.coalesce(func.sum(RefundComponentAllocation.creator_total_reversal_fen), 0)
                    ).where(RefundComponentAllocation.component_allocation_id == component.id)
                )
                or 0
            )
            base_fen = component.base_fen
            if component.allocated_fen <= 0:
                continue
            if item_fen > 0 and cumulative_refund < item_fen:
                target_base = base_fen * cumulative_refund // item_fen
            else:
                target_base = base_fen
            target_base = min(target_base, base_fen)
            base_reduction = max(target_base - previous_base, 0)
            creator_total = component.creator_total_fen
            if target_base >= base_fen:
                target_creator = creator_total
            elif base_fen > 0:
                target_creator = creator_total * target_base // base_fen
            else:
                target_creator = 0
            creator_reversal = max(target_creator - previous_creator, 0)
            if base_reduction == 0 and creator_reversal == 0:
                continue
            platform_reversal = max(base_reduction - creator_reversal, 0)

            component_reversal = RefundComponentAllocation(
                refund_item_id=item.id,
                component_allocation_id=component.id,
                base_reduction_fen=base_reduction,
                creator_total_reversal_fen=creator_reversal,
                platform_reversal_fen=platform_reversal,
            )
            db.add(component_reversal)
            await db.flush()
            platform_reversal_total += platform_reversal

            allocations = list(
                (
                    await db.execute(
                        select(OrderItemAllocation)
                        .where(OrderItemAllocation.component_allocation_id == component.id)
                        .order_by(OrderItemAllocation.beneficiary_party_id)
                    )
                )
                .scalars()
                .all()
            )
            previous_by_allocation: dict[uuid.UUID, int] = {}
            if allocations:
                rows = (
                    await db.execute(
                        select(
                            RefundBeneficiaryReversal.allocation_id,
                            func.coalesce(func.sum(RefundBeneficiaryReversal.creator_reversal_fen), 0),
                        )
                        .join(
                            RefundComponentAllocation,
                            RefundComponentAllocation.id
                            == RefundBeneficiaryReversal.refund_component_allocation_id,
                        )
                        .where(RefundComponentAllocation.component_allocation_id == component.id)
                        .group_by(RefundBeneficiaryReversal.allocation_id)
                    )
                ).all()
                previous_by_allocation = {row[0]: int(row[1]) for row in rows}

            if creator_reversal > 0 and allocations:
                if target_base >= base_fen:
                    # 全退：按剩余份额精确冲尽，无分尾差残留（§19 T17）。
                    per_allocation = {
                        str(allocation.id): max(
                            allocation.creator_fen - previous_by_allocation.get(allocation.id, 0), 0
                        )
                        for allocation in allocations
                    }
                else:
                    per_allocation = _largest_remainder(
                        creator_reversal,
                        [
                            (
                                str(allocation.id),
                                max(
                                    allocation.creator_fen
                                    - previous_by_allocation.get(allocation.id, 0),
                                    0,
                                ),
                            )
                            for allocation in allocations
                        ],
                    )
                for allocation in allocations:
                    fen = per_allocation.get(str(allocation.id), 0)
                    if fen <= 0:
                        continue
                    db.add(
                        RefundBeneficiaryReversal(
                            refund_component_allocation_id=component_reversal.id,
                            allocation_id=allocation.id,
                            creator_reversal_fen=fen,
                        )
                    )
                    creator_reversal_by_party[allocation.beneficiary_party_id] = (
                        creator_reversal_by_party.get(allocation.beneficiary_party_id, 0) + fen
                    )
            await db.flush()
            created.append(component_reversal)

    if created:
        await _post_refund_reversal_journal(
            db, refund, creator_reversal_by_party, platform_reversal_total
        )
    return created


async def _post_refund_reversal_journal(
    db: AsyncSession,
    refund: Refund,
    creator_reversal_by_party: dict[uuid.UUID, int],
    platform_reversal_total: int,
) -> LedgerJournal | None:
    """退款冲回的分录：待成熟/可结算 → 已冲回；`event_key` 唯一保证只冲回一次。"""
    event_key = f"refund_reversal:{refund.id}"
    existing = (
        await db.execute(select(LedgerJournal).where(LedgerJournal.event_key == event_key))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    lines: list[tuple[LedgerAccountRecord, int, int, uuid.UUID | None]] = []
    for party_id in sorted(creator_reversal_by_party, key=str):
        fen = creator_reversal_by_party[party_id]
        if fen <= 0:
            continue
        pending = await _ensure_account(db, party_id, refund.currency, LedgerBucket.PENDING.value)
        reversed_account = await _ensure_account(
            db, party_id, refund.currency, LedgerBucket.REVERSED.value
        )
        lines.append((pending, fen, 0, None))
        lines.append((reversed_account, 0, fen, None))
    if platform_reversal_total > 0:
        platform_available = await _ensure_account(
            db, None, refund.currency, LedgerBucket.AVAILABLE.value
        )
        platform_reversed = await _ensure_account(
            db, None, refund.currency, LedgerBucket.REVERSED.value
        )
        lines.append((platform_available, platform_reversal_total, 0, None))
        lines.append((platform_reversed, 0, platform_reversal_total, None))
    if not lines:
        return None
    journal = LedgerJournal(
        event_key=event_key,
        event_type=LedgerEventType.REFUND_REVERSAL.value,
        source_id=refund.id,
        occurred_at=_now(),
    )
    db.add(journal)
    await db.flush()
    await _write_journal_lines(db, journal, lines)
    return journal


async def _ensure_refund_items(db: AsyncSession, refund: Refund) -> list[RefundItem]:
    """为整单退款生成（或取回）明细退款记录（§11.3、§18.2）。"""
    order_items = list(
        (
            await db.execute(
                select(OrderItem)
                .where(OrderItem.order_id == refund.order_id)
                .order_by(OrderItem.created_at, OrderItem.id)
            )
        )
        .scalars()
        .all()
    )
    items: list[RefundItem] = []
    for order_item in order_items:
        existing = (
            await db.execute(
                select(RefundItem).where(
                    RefundItem.refund_id == refund.id, RefundItem.order_item_id == order_item.id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            amount_fen = _fen(order_item.amount)
            existing = RefundItem(
                refund_id=refund.id,
                order_item_id=order_item.id,
                amount_fen=amount_fen,
                requested_fen=amount_fen,
                succeeded_fen=amount_fen,
                entitlement_action="REVOKE_ITEM",
            )
            db.add(existing)
            await db.flush()
        items.append(existing)
    return items


async def reverse_order_revenue(db: AsyncSession, refund: Refund) -> list[RefundComponentAllocation]:
    """整单退款的收益冲回入口：按明细生成退款明细后冲回（§11.2 首期兼容策略）。"""
    items = await _ensure_refund_items(db, refund)
    return await reverse_revenue_for_refund(db, refund, items)


async def accrue_order_revenue(
    db: AsyncSession, order_id: uuid.UUID
) -> LedgerJournal | None:
    """管理员入口：对某订单执行初始计提（开关关闭时抛 NotEnabled）。"""
    _require_enabled()
    order = await _get_order(db, order_id)
    return await accrue_revenue(db, order)


async def reverse_refund_revenue(
    db: AsyncSession, refund_id: uuid.UUID
) -> list[RefundComponentAllocation]:
    """管理员入口：对某退款执行收益冲回（开关关闭时抛 NotEnabled）。"""
    _require_enabled()
    refund = await _get_refund(db, refund_id)
    return await reverse_order_revenue(db, refund)


# --------------------------------------------------------------------------
# beta15 分配预留与可结算净额（§11.6、§14.4 不变量 5、§19 T22）
# --------------------------------------------------------------------------
async def reserve_allocation(
    db: AsyncSession,
    allocation_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    amount_fen: int,
) -> AllocationReservation:
    """按来源预留某分配的风险金额（§11.6）：同来源不重复预留；锁分配后查余额。

    先锁 allocation 行，再校验 `可结算净额 = creator_fen − 已冲回 − 已预留`；
    同一来源重复预留时幂等返回既有记录。
    """
    _require_enabled()
    if amount_fen <= 0:
        raise InvalidInput("预留金额必须为正", details={"amount_fen": amount_fen})
    allocation = (
        await db.execute(
            select(OrderItemAllocation)
            .where(OrderItemAllocation.id == allocation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if allocation is None:
        raise NotFound("收益分配不存在")

    existing = (
        await db.execute(
            select(AllocationReservation).where(
                AllocationReservation.allocation_id == allocation_id,
                AllocationReservation.source_type == source_type,
                AllocationReservation.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.state == RESERVATION_ACTIVE:
            return existing
        raise Conflict("该来源的预留已释放，不能重复预留", code="RESERVATION_RELEASED")

    reversed_total = int(
        await db.scalar(
            select(func.coalesce(func.sum(RefundBeneficiaryReversal.creator_reversal_fen), 0)).where(
                RefundBeneficiaryReversal.allocation_id == allocation_id
            )
        )
        or 0
    )
    reserved_total = int(
        await db.scalar(
            select(func.coalesce(func.sum(AllocationReservation.amount_fen), 0)).where(
                AllocationReservation.allocation_id == allocation_id,
                AllocationReservation.state == RESERVATION_ACTIVE,
            )
        )
        or 0
    )
    available = allocation.creator_fen - reversed_total - reserved_total
    if amount_fen > available:
        raise Conflict(
            "可结算净额不足",
            code="INSUFFICIENT_SETTLEABLE_AMOUNT",
            details={"available_fen": available, "requested_fen": amount_fen},
        )
    reservation = AllocationReservation(
        allocation_id=allocation_id,
        source_type=source_type,
        source_id=source_id,
        amount_fen=amount_fen,
        state=RESERVATION_ACTIVE,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def release_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> AllocationReservation:
    """释放预留（退款被拒／案件解除）；重复释放幂等。"""
    _require_enabled()
    reservation = (
        await db.execute(
            select(AllocationReservation)
            .where(AllocationReservation.id == reservation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise NotFound("预留记录不存在")
    if reservation.state == RESERVATION_ACTIVE:
        reservation.state = RESERVATION_RELEASED
        await db.flush()
    return reservation


async def settleable_balance(db: AsyncSession, party_id: uuid.UUID, currency: str) -> int:
    """可结算净额 = 可支付 − 已有预留（§14.4 不变量 5）。

    本版未配置成熟规则，待成熟（PENDING）与可结算（AVAILABLE）合并计为可支付；
    减去 `allocation_reservations` 中该主体 allocation 的 active 预留。成熟规则确认后再分桶。
    """
    payable = await _bucket_balance(db, party_id, currency, LedgerBucket.PENDING.value)
    payable += await _bucket_balance(db, party_id, currency, LedgerBucket.AVAILABLE.value)
    reserved = int(
        await db.scalar(
            select(func.coalesce(func.sum(AllocationReservation.amount_fen), 0))
            .join(
                OrderItemAllocation,
                OrderItemAllocation.id == AllocationReservation.allocation_id,
            )
            .where(
                OrderItemAllocation.beneficiary_party_id == party_id,
                AllocationReservation.state == RESERVATION_ACTIVE,
            )
        )
        or 0
    )
    return payable - reserved


# --------------------------------------------------------------------------
# 内部加载与映射
# --------------------------------------------------------------------------
async def _get_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    order = (
        await db.execute(select(Order).where(Order.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise NotFound("订单不存在")
    return order


async def _get_refund(db: AsyncSession, refund_id: uuid.UUID) -> Refund:
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id))
    ).scalar_one_or_none()
    if refund is None:
        raise NotFound("退款记录不存在")
    return refund


async def _withdrawal_out(db: AsyncSession, withdrawal_id: uuid.UUID) -> WithdrawalOut:
    withdrawal = (
        await db.execute(select(Withdrawal).where(Withdrawal.id == withdrawal_id))
    ).scalar_one_or_none()
    if withdrawal is None:
        raise NotFound("提现记录不存在")
    return WithdrawalOut.model_validate(withdrawal)


async def _refund_out(db: AsyncSession, refund: Refund) -> RefundOut:
    return RefundOut.model_validate(refund)


async def _order_out(db: AsyncSession, order: Order) -> OrderOut:
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
    )
    dto = OrderOut.model_validate(order)
    return dto.model_copy(
        update={"items": [OrderItemOut.model_validate(item) for item in items]}
    )
