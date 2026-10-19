"""beta15 commerce 明细级收益分配、退款冲回与收益子账测试（虚构数据）。

对应《用户权责、内容治理与授权收益体系设计文档》§19：
T13 确认事务整体回滚、T14 一次计提、T15 多组件/多人分配守恒、T16/T17 退款累计冲回、
T22 同一收益并发入批不超可付净额；并覆盖退款 scope 兼容与开关关闭时抛 NotEnabled。

隔离 SQLite + create_all；JSONB 在测试库编译为 JSON。不接任何真实支付渠道。
"""

import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.models_registry  # noqa: F401
from app.config import settings
from app.core.enums import OrderStatus, RefundStatus
from app.core.errors import Conflict, NotEnabled
from app.db import Base
from app.modules.access.models import Entitlement
from app.modules.commerce import service
from app.modules.commerce.models import (
    AllocationReservation,
    LedgerJournal,
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
)
from app.modules.commerce.schemas import RefundCreate
from app.modules.identity.models import User


@compiles(JSONB, "sqlite")
def jsonb_for_test(type_, compiler, **kwargs):
    return "JSON"


async def _count(db, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


class CommerceAllocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        self.db = self.factory()
        self._saved_enabled = settings.COMMERCE_ENABLED
        self._saved_gate = settings.COMMERCE_COMPLIANCE_GATE_CLOSED

        self.creator_a = User(email="a@example.test", password_hash="x", role="contributor", status="active")
        self.creator_b = User(email="b@example.test", password_hash="x", role="contributor", status="active")
        self.buyer = User(email="buyer@example.test", password_hash="x", role="user", status="active")
        self.db.add_all([self.creator_a, self.creator_b, self.buyer])
        await self.db.flush()

    async def asyncTearDown(self):
        settings.COMMERCE_ENABLED = self._saved_enabled
        settings.COMMERCE_COMPLIANCE_GATE_CLOSED = self._saved_gate
        await self.db.close()
        await self.engine.dispose()

    def enable(self, *, gate_closed: bool = True) -> None:
        settings.COMMERCE_ENABLED = True
        settings.COMMERCE_COMPLIANCE_GATE_CLOSED = gate_closed

    # --- 构造工具（虚构数据） -------------------------------------------------
    async def _product(self, owner, amount="100.00", currency="CNY"):
        product = Product(
            kind="single_resource",
            title="demo",
            owner_id=owner.id,
            publication_status="published",
            grant_scope_type="explicit_grant",
            grant_scope_id=uuid.uuid4(),
            grant_capability="read_full",
        )
        self.db.add(product)
        await self.db.flush()
        price = Price(product_id=product.id, currency=currency, amount=Decimal(amount), version=1)
        self.db.add(price)
        await self.db.flush()
        return product, price

    async def _order(self, amount="100.00", currency="CNY"):
        order = Order(
            user_id=self.buyer.id,
            status=OrderStatus.PENDING.value,
            currency=currency,
            total_amount=Decimal(amount),
        )
        self.db.add(order)
        await self.db.flush()
        return order

    async def _item(self, order, product, price, amount, *, snapshot=None):
        item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            price_id=price.id,
            title="demo",
            quantity=1,
            unit_amount=Decimal(amount),
            amount=Decimal(amount),
            currency=order.currency,
            grant_scope_type="explicit_grant",
            grant_scope_id=product.grant_scope_id,
            grant_capability="read_full",
            composition_snapshot=snapshot,
        )
        self.db.add(item)
        await self.db.flush()
        return item

    def _spec(self, key, *, weight=1, bps=8000, deduction=0, beneficiaries=()):
        return service.ComponentSpec(
            component_key=key,
            weight=weight,
            creator_bps=bps,
            deduction_fen=deduction,
            beneficiaries=tuple(
                service.BeneficiaryShare(beneficiary_party_id=party, share_bps=share)
                for party, share in beneficiaries
            ),
        )

    async def _succeeded_attempt(self, order):
        attempt = PaymentAttempt(
            order_id=order.id,
            channel="manual",
            status="succeeded",
            amount=order.total_amount,
            currency=order.currency,
            channel_txn_id=f"txn-{uuid.uuid4()}",
        )
        self.db.add(attempt)
        await self.db.flush()
        return attempt

    # --- T15：多组件/多人分配含分尾差，守恒且重跑一致 -------------------------
    async def test_t15_multi_component_multi_beneficiary_conserved_and_stable(self):
        self.enable()
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        item = await self._item(order, product, price, "100.00")

        specs = [
            self._spec("a", weight=1, bps=8000, beneficiaries=[(self.creator_a.id, 5000), (self.creator_b.id, 5000)]),
            self._spec("b", weight=1, bps=8000, beneficiaries=[(self.creator_a.id, 10000)]),
            self._spec("c", weight=1, bps=8000, beneficiaries=[(self.creator_b.id, 10000)]),
        ]
        first = await service.allocate_order_item(self.db, item, specs)

        self.assertEqual(sum(c.allocated_fen for c in first), 10000)
        for component in first:
            self.assertEqual(component.base_fen - component.deduction_fen, component.creator_total_fen + component.platform_fen)
            allocations = list(
                (
                    await self.db.execute(
                        select(OrderItemAllocation).where(
                            OrderItemAllocation.component_allocation_id == component.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(sum(a.creator_fen for a in allocations), component.creator_total_fen)

        # 重跑：顺序与结果一致（幂等返回既有行）。
        second = await service.allocate_order_item(self.db, item, specs)
        self.assertEqual(
            [(c.component_key, c.allocated_fen, c.creator_total_fen, c.platform_fen) for c in first],
            [(c.component_key, c.allocated_fen, c.creator_total_fen, c.platform_fen) for c in second],
        )
        self.assertEqual(await _count(self.db, OrderItemComponentAllocation), 3)

    # --- T14：重复提交/超时重试 → 一次计提（event_key 唯一） ------------------
    async def test_t14_accrual_is_idempotent_by_event_key(self):
        self.enable()
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        snapshot = {
            "components": [
                {
                    "component_key": "a",
                    "weight": 1,
                    "creator_bps": 8000,
                    "beneficiaries": [{"party_id": str(self.creator_a.id), "share_bps": 10000}],
                }
            ]
        }
        item = await self._item(order, product, price, "100.00", snapshot=snapshot)
        await service.allocate_order_item(self.db, item, service._component_specs_from_snapshot(snapshot))

        first = await service.accrue_revenue(self.db, order)
        second = await service.accrue_revenue(self.db, order)
        self.assertIsNotNone(first)
        self.assertEqual(first.id, second.id)
        self.assertEqual(await _count(self.db, LedgerJournal), 1)

    # --- T13：确认事务在收益落账时失败 → 整体回滚 ----------------------------
    async def test_t13_confirmation_rolls_back_on_accrual_failure(self):
        self.enable()
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        snapshot = {
            "components": [
                {
                    "component_key": "a",
                    "weight": 1,
                    "creator_bps": 8000,
                    "beneficiaries": [{"party_id": str(self.creator_a.id), "share_bps": 10000}],
                }
            ]
        }
        await self._item(order, product, price, "100.00", snapshot=snapshot)
        order_id = order.id
        # 固化前置数据，模拟请求事务失败后的整体回滚（get_db 在异常时 rollback）。
        await self.db.commit()

        with patch.object(service, "allocate_order_item", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                await service.confirm_payment(
                    self.db,
                    channel="manual",
                    payload={
                        "signature_verified": True,
                        "order_ref": str(order_id),
                        "channel_txn_id": "txn-rollback",
                        "amount": "100.00",
                        "currency": "CNY",
                        "status": "succeeded",
                    },
                )
        await self.db.rollback()

        reloaded = await self.db.get(Order, order_id)
        self.assertEqual(reloaded.status, OrderStatus.PENDING.value)
        self.assertEqual(await _count(self.db, PaymentAttempt), 0)
        self.assertEqual(await _count(self.db, Entitlement), 0)
        self.assertEqual(await _count(self.db, OrderItemComponentAllocation), 0)
        self.assertEqual(await _count(self.db, LedgerJournal), 0)

    # --- T16：完整退 150 元示例订单，分项冲回 A/B ----------------------------
    async def test_t16_full_refund_reverses_each_beneficiary(self):
        self.enable()
        order = await self._order("150.00")
        product, price = await self._product(self.creator_a, "100.00")
        item_a = await self._item(order, product, price, "100.00")
        item_b = await self._item(order, product, price, "50.00")
        await service.allocate_order_item(
            self.db, item_a, [self._spec("a", bps=8000, beneficiaries=[(self.creator_a.id, 10000)])]
        )
        await service.allocate_order_item(
            self.db, item_b, [self._spec("b", bps=8500, beneficiaries=[(self.creator_b.id, 10000)])]
        )

        refund = Refund(
            order_id=order.id,
            requested_by=self.buyer.id,
            status=RefundStatus.SUCCEEDED.value,
            amount=Decimal("150.00"),
            currency="CNY",
            reason="整单退款",
        )
        self.db.add(refund)
        await self.db.flush()
        items = [
            RefundItem(refund_id=refund.id, order_item_id=item_a.id, amount_fen=10000, requested_fen=10000),
            RefundItem(refund_id=refund.id, order_item_id=item_b.id, amount_fen=5000, requested_fen=5000),
        ]
        self.db.add_all(items)
        await self.db.flush()

        await service.reverse_revenue_for_refund(self.db, refund, items)

        reversals = {
            row.creator_total_reversal_fen
            for row in (await self.db.execute(select(RefundComponentAllocation))).scalars().all()
        }
        self.assertEqual(reversals, {8000, 4250})
        beneficiary_total = int(
            await self.db.scalar(select(func.coalesce(func.sum(RefundBeneficiaryReversal.creator_reversal_fen), 0)))
            or 0
        )
        self.assertEqual(beneficiary_total, 8000 + 4250)

    # --- T17：多次部分退款累计达到全退，无分尾差残留 -------------------------
    async def test_t17_partial_refunds_accumulate_to_full_without_residual(self):
        self.enable()
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        item = await self._item(order, product, price, "100.00")
        await service.allocate_order_item(
            self.db, item, [self._spec("a", bps=8000, beneficiaries=[(self.creator_a.id, 10000)])]
        )

        async def partial(amount_fen: int):
            refund = Refund(
                order_id=order.id,
                requested_by=self.buyer.id,
                status=RefundStatus.SUCCEEDED.value,
                amount=Decimal(amount_fen) / 100,
                currency="CNY",
                reason="部分退款",
            )
            self.db.add(refund)
            await self.db.flush()
            refund_item = RefundItem(
                refund_id=refund.id, order_item_id=item.id, amount_fen=amount_fen, requested_fen=amount_fen
            )
            self.db.add(refund_item)
            await self.db.flush()
            await service.reverse_revenue_for_refund(self.db, refund, [refund_item])

        await partial(2500)
        first_creator = int(
            await self.db.scalar(select(func.coalesce(func.sum(RefundComponentAllocation.creator_total_reversal_fen), 0))) or 0
        )
        self.assertEqual(first_creator, 2000)

        await partial(7500)
        creator_total = int(
            await self.db.scalar(select(func.coalesce(func.sum(RefundComponentAllocation.creator_total_reversal_fen), 0))) or 0
        )
        platform_total = int(
            await self.db.scalar(select(func.coalesce(func.sum(RefundComponentAllocation.platform_reversal_fen), 0))) or 0
        )
        self.assertEqual(creator_total, 8000)  # 完全冲尽，无残留
        self.assertEqual(platform_total, 2000)  # 平台冲回只计一次

        allocation = (await self.db.execute(select(OrderItemAllocation))).scalars().one()
        reversed_for_allocation = int(
            await self.db.scalar(
                select(func.coalesce(func.sum(RefundBeneficiaryReversal.creator_reversal_fen), 0)).where(
                    RefundBeneficiaryReversal.allocation_id == allocation.id
                )
            )
            or 0
        )
        self.assertEqual(reversed_for_allocation, allocation.creator_fen)

    # --- T22：同一收益并发进入两个批次 → 不超可付净额 ------------------------
    async def test_t22_reservations_do_not_exceed_settleable_balance(self):
        self.enable()
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        snapshot = {
            "components": [
                {
                    "component_key": "a",
                    "weight": 1,
                    "creator_bps": 10000,
                    "beneficiaries": [{"party_id": str(self.creator_a.id), "share_bps": 10000}],
                }
            ]
        }
        item = await self._item(order, product, price, "100.00", snapshot=snapshot)
        await service.allocate_order_item(self.db, item, service._component_specs_from_snapshot(snapshot))
        await service.accrue_revenue(self.db, order)

        allocation = (await self.db.execute(select(OrderItemAllocation))).scalars().one()
        self.assertEqual(await service.settleable_balance(self.db, self.creator_a.id, "CNY"), 10000)

        await service.reserve_allocation(self.db, allocation.id, "settlement", uuid.uuid4(), 6000)
        self.assertEqual(await service.settleable_balance(self.db, self.creator_a.id, "CNY"), 4000)

        with self.assertRaises(Conflict) as ctx:
            await service.reserve_allocation(self.db, allocation.id, "settlement", uuid.uuid4(), 6000)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_SETTLEABLE_AMOUNT")

        reservation = (await self.db.execute(select(AllocationReservation))).scalars().one()
        await service.release_reservation(self.db, reservation.id)
        self.assertEqual(await service.settleable_balance(self.db, self.creator_a.id, "CNY"), 10000)

    # --- 退款 scope 兼容（T34/T12 回归） --------------------------------------
    async def test_order_items_scope_unsupported_without_silent_full_refund(self):
        self.enable(gate_closed=True)
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        item = await self._item(order, product, price, "100.00")
        order.status = OrderStatus.PAID.value
        await self._succeeded_attempt(order)
        await self.db.flush()

        payload = RefundCreate(
            order_id=order.id,
            reason="明细争议",
            scope="ORDER_ITEMS",
            items=[{"order_item_id": item.id, "amount_fen": 10000, "requested_action": "REVOKE_ITEM"}],
        )
        with self.assertRaises(Conflict) as ctx:
            await service.create_refund(
                self.db, user=self.buyer, payload=payload, request_id="t", idempotency_key="k1"
            )
        self.assertEqual(ctx.exception.code, "REFUND_SCOPE_UNSUPPORTED")
        # 未静默退整单：未创建任何退款记录。
        self.assertEqual(await _count(self.db, Refund), 0)

    async def test_full_order_scope_keeps_original_semantics(self):
        self.enable(gate_closed=True)
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        await self._item(order, product, price, "100.00")
        order.status = OrderStatus.PAID.value
        await self._succeeded_attempt(order)
        await self.db.flush()

        payload = RefundCreate(order_id=order.id, reason="整单退款", scope="FULL_ORDER")
        # 渠道未配置：整单退款仍按原语义失败，不假成功。
        with self.assertRaises(NotEnabled):
            await service.create_refund(
                self.db, user=self.buyer, payload=payload, request_id="t", idempotency_key="k2"
            )

    # --- 开关关闭时新写接口抛 NotEnabled -------------------------------------
    async def test_new_write_apis_raise_not_enabled_when_closed(self):
        settings.COMMERCE_ENABLED = False
        order = await self._order("100.00")
        product, price = await self._product(self.creator_a, "100.00")
        item = await self._item(order, product, price, "100.00")
        spec = self._spec("a", bps=8000, beneficiaries=[(self.creator_a.id, 10000)])

        with self.assertRaises(NotEnabled):
            await service.allocate_order_item(self.db, item, [spec])
        with self.assertRaises(NotEnabled):
            await service.accrue_revenue(self.db, order)
        with self.assertRaises(NotEnabled):
            await service.reserve_allocation(self.db, uuid.uuid4(), "settlement", uuid.uuid4(), 100)
        with self.assertRaises(NotEnabled):
            await service.release_reservation(self.db, uuid.uuid4())
        refund = Refund(
            order_id=order.id,
            requested_by=self.buyer.id,
            status=RefundStatus.REQUESTED.value,
            amount=Decimal("100.00"),
            currency="CNY",
            reason="r",
        )
        with self.assertRaises(NotEnabled):
            await service.reverse_revenue_for_refund(self.db, refund, [])


if __name__ == "__main__":
    unittest.main()
