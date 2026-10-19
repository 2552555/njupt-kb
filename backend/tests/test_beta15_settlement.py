"""beta15 settlement 结算账本、付款与对账测试（虚构数据）。

对应《用户权责、内容治理与授权收益体系设计文档》§19：
T19 退款在途不入批、T21 已付后退款建追偿、T22 同一收益并发入批不超可付净额、
T23 付款不确定持续占款且真实转出键唯一、T24 已锁批与冻结不同时可用；
并覆盖开关关闭时写接口抛 NotEnabled、未配置条款不产生默认值、批准后变更新版本、
本人列表只返回本人且账户脱敏、异议冻结、账务更正关联原记录。

隔离 SQLite + create_all；JSONB 在测试库编译为 JSON。不接任何真实支付渠道、不新增伪回调。
"""

import unittest
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.models_registry  # noqa: F401
from app.config import settings
from app.core.enums import PayoutStatus, RefundStatus, SettlementStatementState
from app.core.errors import Conflict, InvalidInput, NotEnabled
from app.db import Base
from app.modules.commerce import service as commerce
from app.modules.commerce.models import (
    AllocationReservation,
    LedgerJournal,
    Order,
    OrderItem,
    OrderItemAllocation,
    OrderItemComponentAllocation,
    Price,
    Product,
    Refund,
    RefundBeneficiaryReversal,
    RefundItem,
)
from app.modules.identity.models import User
from app.modules.settlement import service as settlement
from app.modules.settlement.models import (
    Payout,
    RecoveryClaim,
    SettlementItem,
    SettlementStatement,
)
from app.modules.settlement.schemas import (
    DisputeItemIn,
    LedgerCorrectionIn,
    PayoutAttemptCreateIn,
    PayoutReconcileIn,
    StatementApproveIn,
    StatementConfirmIn,
    StatementDisputeIn,
    StatementGenerateIn,
)

PERIOD = "2026-10"


@compiles(JSONB, "sqlite")
def jsonb_for_test(type_, compiler, **kwargs):
    return "JSON"


async def _count(db, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


class SettlementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        self.db = self.factory()

        self._saved = (
            settings.COMMERCE_ENABLED,
            settings.COMMERCE_COMPLIANCE_GATE_CLOSED,
            settings.REVENUE_DEFAULT_CREATOR_BPS,
            settings.SETTLEMENT_MIN_PAYOUT_FEN,
        )

        self.creator_a = User(email="a@example.test", password_hash="x", role="contributor", status="active")
        self.creator_b = User(email="b@example.test", password_hash="x", role="contributor", status="active")
        self.buyer = User(email="buyer@example.test", password_hash="x", role="user", status="active")
        self.admin = User(email="admin@example.test", password_hash="x", role="admin", status="active")
        self.db.add_all([self.creator_a, self.creator_b, self.buyer, self.admin])
        await self.db.flush()

    async def asyncTearDown(self):
        (
            settings.COMMERCE_ENABLED,
            settings.COMMERCE_COMPLIANCE_GATE_CLOSED,
            settings.REVENUE_DEFAULT_CREATOR_BPS,
            settings.SETTLEMENT_MIN_PAYOUT_FEN,
        ) = self._saved
        await self.db.close()
        await self.engine.dispose()

    def enable(self, *, terms: bool = True) -> None:
        """开启资金能力并配置结算条款（测试用显式值，不是生产默认比例）。"""
        settings.COMMERCE_ENABLED = True
        settings.COMMERCE_COMPLIANCE_GATE_CLOSED = False
        if terms:
            settings.REVENUE_DEFAULT_CREATOR_BPS = 8000
            settings.SETTLEMENT_MIN_PAYOUT_FEN = 100

    # --- 构造工具（虚构数据） -------------------------------------------------
    async def _product(self, owner, amount="100.00"):
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
        price = Price(product_id=product.id, currency="CNY", amount=Decimal(amount), version=1)
        self.db.add(price)
        await self.db.flush()
        return product, price

    async def _order(self, amount="100.00"):
        order = Order(
            user_id=self.buyer.id,
            status="pending",
            currency="CNY",
            total_amount=Decimal(amount),
        )
        self.db.add(order)
        await self.db.flush()
        return order

    async def _item(self, order, product, price, amount):
        item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            price_id=price.id,
            title="demo",
            quantity=1,
            unit_amount=Decimal(amount),
            amount=Decimal(amount),
            currency="CNY",
            grant_scope_type="explicit_grant",
            grant_scope_id=product.grant_scope_id,
            grant_capability="read_full",
        )
        self.db.add(item)
        await self.db.flush()
        return item

    async def _accrued_allocation(self, party, amount="100.00"):
        """构造一条已计提的受益人分配：明细实收全额归属该主体。"""
        order = await self._order(amount)
        product, price = await self._product(party, amount)
        item = await self._item(order, product, price, amount)
        spec = commerce.ComponentSpec(
            component_key="c",
            weight=1,
            creator_bps=10000,
            beneficiaries=(commerce.BeneficiaryShare(beneficiary_party_id=party.id, share_bps=10000),),
        )
        await commerce.allocate_order_item(self.db, item, [spec])
        await commerce.accrue_revenue(self.db, order)
        allocation = (
            await self.db.execute(
                select(OrderItemAllocation)
                .join(
                    OrderItemComponentAllocation,
                    OrderItemComponentAllocation.id
                    == OrderItemAllocation.component_allocation_id,
                )
                .where(OrderItemComponentAllocation.order_item_id == item.id)
            )
        ).scalars().one()
        return order, item, allocation

    async def _generate(self, payee, *, period=PERIOD, currency="CNY"):
        return await settlement.generate_statement(
            self.db,
            actor=self.admin,
            payload=StatementGenerateIn(payee_party_id=payee.id, period=period, currency=currency),
            request_id="t",
        )

    async def _approve(self, statement_id, *, version=1, snapshot=None):
        return await settlement.approve_statement(
            self.db,
            actor=self.admin,
            statement_id=statement_id,
            payload=StatementApproveIn(expected_version=version, payee_snapshot=snapshot),
            request_id="t",
        )

    async def _payout_of(self, statement_id) -> Payout:
        return (
            await self.db.execute(select(Payout).where(Payout.statement_version_id == statement_id))
        ).scalar_one()

    # --- 开关关闭：写接口抛 NotEnabled，读接口可用 -----------------------------
    async def test_write_endpoints_raise_not_enabled_when_closed(self):
        settings.COMMERCE_ENABLED = False
        settings.COMMERCE_COMPLIANCE_GATE_CLOSED = True

        with self.assertRaises(NotEnabled):
            await self._generate(self.creator_a)
        with self.assertRaises(NotEnabled):
            await settlement.confirm_statement(
                self.db,
                user=self.creator_a,
                statement_id=uuid.uuid4(),
                payload=StatementConfirmIn(expected_version=1),
                request_id="t",
            )
        with self.assertRaises(NotEnabled):
            await settlement.dispute_statement(
                self.db,
                user=self.creator_a,
                statement_id=uuid.uuid4(),
                payload=StatementDisputeIn(
                    expected_version=1,
                    reason="r",
                    items=[DisputeItemIn(allocation_id=uuid.uuid4(), amount_fen=1)],
                ),
                request_id="t",
            )
        with self.assertRaises(NotEnabled):
            await settlement.approve_statement(
                self.db,
                actor=self.admin,
                statement_id=uuid.uuid4(),
                payload=StatementApproveIn(expected_version=1),
                request_id="t",
            )
        with self.assertRaises(NotEnabled):
            await settlement.create_payout_attempt(
                self.db,
                actor=self.admin,
                payout_id=uuid.uuid4(),
                payload=PayoutAttemptCreateIn(),
                request_id="t",
            )
        with self.assertRaises(NotEnabled):
            await settlement.reconcile_payout_attempt(
                self.db,
                actor=self.admin,
                attempt_id=uuid.uuid4(),
                payload=PayoutReconcileIn(result="failed", evidence_ref="e"),
                request_id="t",
            )
        with self.assertRaises(NotEnabled):
            await settlement.record_ledger_correction(
                self.db,
                actor=self.admin,
                payload=LedgerCorrectionIn(original_journal_id=uuid.uuid4(), reason="r"),
                request_id="t",
            )
        with self.assertRaises(NotEnabled):
            await settlement.record_recovery_claims(
                self.db, refund_id=uuid.uuid4(), legal_basis_ref="合同"
            )

        # 只读接口不涉及资金，开关关闭仍可用（返回空列表）。
        page = await settlement.list_my_statements(self.db, user=self.creator_a, limit=None, cursor=None)
        self.assertEqual(page.items, [])
        self.assertEqual(await _count(self.db, SettlementStatement), 0)

    # --- 未配置条款：不产生默认比例/最低额 -------------------------------------
    async def test_default_terms_not_configured(self):
        settings.COMMERCE_ENABLED = True
        settings.COMMERCE_COMPLIANCE_GATE_CLOSED = False
        settings.REVENUE_DEFAULT_CREATOR_BPS = 0
        settings.SETTLEMENT_MIN_PAYOUT_FEN = 0
        await self._accrued_allocation(self.creator_a)

        # 分成比例未配置 → NotEnabled，不套用 80/20 或任何示例值。
        with self.assertRaises(NotEnabled):
            await self._generate(self.creator_a)
        # 比例配置但最低付款额未配置 → 仍 NotEnabled。
        settings.REVENUE_DEFAULT_CREATOR_BPS = 8000
        with self.assertRaises(NotEnabled):
            await self._generate(self.creator_a)
        self.assertEqual(await _count(self.db, SettlementStatement), 0)

    # --- T19：退款在途（processing/uncertain）金额不入批、不重复退款 -----------
    async def test_t19_refund_in_flight_excluded_from_statement(self):
        self.enable()
        _, _, allocation = await self._accrued_allocation(self.creator_a, "100.00")
        refund_id = uuid.uuid4()
        # 退款在途：按分配预留风险金额（commerce 语义；未确认退款不直接认定作者损失）。
        await commerce.reserve_allocation(self.db, allocation.id, "refund", refund_id, 4000)
        self.assertEqual(await commerce.settleable_balance(self.db, self.creator_a.id, "CNY"), 6000)

        statement = await self._generate(self.creator_a)
        self.assertEqual(statement.gross_fen, 6000)
        self.assertEqual(statement.net_fen, 6000)
        items = list(
            (await self.db.execute(select(SettlementItem).where(SettlementItem.statement_id == statement.id)))
            .scalars()
            .all()
        )
        self.assertEqual(sum(i.amount_fen for i in items), 6000)

        # 不重复退款：同一来源重复预留是幂等的，不产生第二条预留。
        await commerce.reserve_allocation(self.db, allocation.id, "refund", refund_id, 4000)
        reservations = list(
            (
                await self.db.execute(
                    select(AllocationReservation).where(
                        AllocationReservation.allocation_id == allocation.id,
                        AllocationReservation.source_type == "refund",
                    )
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(len(reservations), 1)

    # --- T22：同一收益进入两个批次 → 总锁定不超可付净额 ------------------------
    async def test_t22_same_allocation_two_batches(self):
        self.enable()
        _, _, allocation = await self._accrued_allocation(self.creator_a, "100.00")

        v1 = await self._generate(self.creator_a)
        v2 = await self._generate(self.creator_a)  # 草稿未锁定，可同时包含同一 allocation
        self.assertEqual((v1.version, v2.version), (1, 2))

        await self._approve(v1.id, version=1)  # 锁批成功
        with self.assertRaises(Conflict) as ctx:
            await self._approve(v2.id, version=2)  # 第二批次超额
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_SETTLEABLE_AMOUNT")

        locked = int(
            await self.db.scalar(
                select(func.coalesce(func.sum(AllocationReservation.amount_fen), 0)).where(
                    AllocationReservation.allocation_id == allocation.id,
                    AllocationReservation.state == "active",
                )
            )
            or 0
        )
        self.assertLessEqual(locked, allocation.creator_fen)
        self.assertEqual(await commerce.settleable_balance(self.db, self.creator_a.id, "CNY"), 0)

    # --- T23：uncertain 持续占款，不释放、不新建 attempt -----------------------
    async def test_t23_uncertain_keeps_funds_held(self):
        self.enable()
        _, _, allocation = await self._accrued_allocation(self.creator_a, "100.00")
        statement = await self._generate(self.creator_a)
        await self._approve(statement.id, version=1, snapshot={"account": "6222020200112233445"})
        payout = await self._payout_of(statement.id)

        attempt_out = await settlement.create_payout_attempt(
            self.db, actor=self.admin, payout_id=payout.id, payload=PayoutAttemptCreateIn(), request_id="t"
        )
        self.assertEqual(attempt_out.state, PayoutStatus.PROCESSING.value)

        reconciled = await settlement.reconcile_payout_attempt(
            self.db,
            actor=self.admin,
            attempt_id=attempt_out.id,
            payload=PayoutReconcileIn(result="uncertain", evidence_ref="bank-timeout", reason="外部超时"),
            request_id="t",
        )
        self.assertEqual(reconciled.state, PayoutStatus.UNCERTAIN.value)
        self.assertEqual((await self.db.get(Payout, payout.id)).state, PayoutStatus.UNCERTAIN.value)
        # 占款不释放：可结算净额仍为 0。
        self.assertEqual(await commerce.settleable_balance(self.db, self.creator_a.id, "CNY"), 0)
        # 不确定时不得新建 attempt。
        with self.assertRaises(Conflict) as ctx:
            await settlement.create_payout_attempt(
                self.db, actor=self.admin, payout_id=payout.id, payload=PayoutAttemptCreateIn(), request_id="t"
            )
        self.assertEqual(ctx.exception.code, "PAYOUT_NOT_ATTEMPTABLE")
        # 恢复备份后待人工核对清单含该笔（只读，不重放外部转账）。
        pending = await settlement.reconcile_pending_payouts(self.db)
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]["requires_manual_reconciliation"])

        # 查明后在原尝试上收口，不能另建转账来绕过不确定状态。
        attempts = await settlement.list_payout_attempts(self.db, payout_id=payout.id, limit=None, cursor=None)
        self.assertEqual([row.id for row in attempts.items], [attempt_out.id])
        result = await settlement.reconcile_payout_attempt(
            self.db, actor=self.admin, attempt_id=attempt_out.id,
            payload=PayoutReconcileIn(result="succeeded", evidence_ref="核实回单", external_ref_key="UNCERTAIN-RESOLVED"),
            request_id="t",
        )
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(await settlement.reconcile_pending_payouts(self.db), [])

    # --- T23：真实转出唯一键不重复付款 ----------------------------------------
    async def test_t23_duplicate_external_reference_rejected(self):
        self.enable()
        await self._accrued_allocation(self.creator_a, "100.00")

        first = await self._generate(self.creator_a)
        await self._approve(first.id, version=1, snapshot={"account": "6222020200112233445"})
        payout1 = await self._payout_of(first.id)
        attempt1 = await settlement.create_payout_attempt(
            self.db, actor=self.admin, payout_id=payout1.id, payload=PayoutAttemptCreateIn(), request_id="t"
        )
        await settlement.reconcile_payout_attempt(
            self.db,
            actor=self.admin,
            attempt_id=attempt1.id,
            payload=PayoutReconcileIn(result="succeeded", evidence_ref="回单A", external_ref_key="TXN-1"),
            request_id="t",
        )

        await self._accrued_allocation(self.creator_a, "50.00")
        second = await self._generate(self.creator_a)
        await self._approve(second.id, version=2, snapshot={"account": "6222020200112233445"})
        payout2 = await self._payout_of(second.id)
        attempt2 = await settlement.create_payout_attempt(
            self.db, actor=self.admin, payout_id=payout2.id, payload=PayoutAttemptCreateIn(), request_id="t"
        )
        with self.assertRaises(Conflict) as ctx:
            await settlement.reconcile_payout_attempt(
                self.db,
                actor=self.admin,
                attempt_id=attempt2.id,
                payload=PayoutReconcileIn(result="succeeded", evidence_ref="回单B", external_ref_key="TXN-1"),
                request_id="t",
            )
        self.assertEqual(ctx.exception.code, "DUPLICATE_PAYOUT_REFERENCE")

    # --- T24：已锁批时出现退款/侵权冻结 → 不同时可用又冻结 ---------------------
    async def test_t24_locked_batch_blocks_case_freeze(self):
        self.enable()
        _, _, allocation = await self._accrued_allocation(self.creator_a, "100.00")
        statement = await self._generate(self.creator_a)
        await self._approve(statement.id, version=1)

        # 批次已锁定全部金额：案件冻结无法再占用同一金额（不同时可用又冻结）。
        with self.assertRaises(Conflict) as ctx:
            await commerce.reserve_allocation(self.db, allocation.id, "case", uuid.uuid4(), 10000)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_SETTLEABLE_AMOUNT")

    async def test_t24_case_freeze_first_blocks_batch(self):
        self.enable()
        _, _, allocation = await self._accrued_allocation(self.creator_a, "100.00")
        await commerce.reserve_allocation(self.db, allocation.id, "case", uuid.uuid4(), 10000)
        # 冻结金额不能进入新结算批次。
        with self.assertRaises(Conflict) as ctx:
            await self._generate(self.creator_a)
        self.assertEqual(ctx.exception.code, "NO_SETTLEABLE_AMOUNT")

    # --- T21：已付款报酬随后退款 → 已付历史保留，追偿单独记录 -------------------
    async def test_t21_paid_then_refund_records_recovery_claim(self):
        self.enable()
        order, item, allocation = await self._accrued_allocation(self.creator_a, "100.00")
        statement = await self._generate(self.creator_a)
        await self._approve(statement.id, version=1, snapshot={"account": "6222020200112233445"})
        payout = await self._payout_of(statement.id)
        attempt = await settlement.create_payout_attempt(
            self.db, actor=self.admin, payout_id=payout.id, payload=PayoutAttemptCreateIn(), request_id="t"
        )
        await settlement.reconcile_payout_attempt(
            self.db,
            actor=self.admin,
            attempt_id=attempt.id,
            payload=PayoutReconcileIn(result="succeeded", evidence_ref="回单", external_ref_key="TXN-9"),
            request_id="t",
        )
        self.assertEqual((await self.db.get(SettlementStatement, statement.id)).state, SettlementStatementState.PAID.value)

        # 随后整单退款：commerce 按明细分项冲回，生成受益人冲回记录。
        refund = Refund(
            order_id=order.id,
            requested_by=self.buyer.id,
            status=RefundStatus.SUCCEEDED.value,
            amount=Decimal("100.00"),
            currency="CNY",
            reason="整单退款",
        )
        self.db.add(refund)
        await self.db.flush()
        refund_item = RefundItem(
            refund_id=refund.id, order_item_id=item.id, amount_fen=10000, requested_fen=10000
        )
        self.db.add(refund_item)
        await self.db.flush()
        await commerce.reverse_revenue_for_refund(self.db, refund, [refund_item])

        # 无依据不得自动扣：追偿必须给出依据。
        with self.assertRaises(InvalidInput):
            await settlement.record_recovery_claims(self.db, refund_id=refund.id, legal_basis_ref="  ")

        claims = await settlement.record_recovery_claims(
            self.db, refund_id=refund.id, legal_basis_ref="许可协议第7条退款追偿", actor_id=self.admin.id
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].amount_fen, allocation.creator_fen)
        self.assertEqual(claims[0].paid_reference, str(statement.id))
        # 已付历史保留：结算单仍为已付，已付事实不被改写。
        self.assertEqual((await self.db.get(SettlementStatement, statement.id)).state, SettlementStatementState.PAID.value)
        # 幂等：再次记录不产生重复追偿。
        again = await settlement.record_recovery_claims(
            self.db, refund_id=refund.id, legal_basis_ref="许可协议第7条退款追偿"
        )
        self.assertEqual(len(again), 1)
        self.assertEqual(await _count(self.db, RecoveryClaim), 1)
        self.assertEqual(await _count(self.db, RefundBeneficiaryReversal), 1)

    # --- 批准后变更新版：旧版本保留，不原地改 ---------------------------------
    async def test_approved_statement_new_version_preserves_old(self):
        self.enable()
        await self._accrued_allocation(self.creator_a, "100.00")
        v1 = await self._generate(self.creator_a)
        await self._approve(v1.id, version=1)

        await self._accrued_allocation(self.creator_a, "50.00")
        v2 = await self._generate(self.creator_a)
        self.assertEqual(v2.version, 2)

        old = await self.db.get(SettlementStatement, v1.id)
        self.assertEqual(old.version, 1)
        self.assertEqual(old.gross_fen, 10000)
        self.assertEqual(old.state, SettlementStatementState.APPROVED.value)
        self.assertEqual(v2.gross_fen, 5000)
        self.assertEqual(await _count(self.db, SettlementStatement), 2)

    # --- 本人列表只返回本人且账户脱敏 -----------------------------------------
    async def test_my_statements_only_own_and_masked(self):
        self.enable()
        await self._accrued_allocation(self.creator_a, "100.00")
        await self._accrued_allocation(self.creator_b, "80.00")
        a_stmt = await self._generate(self.creator_a)
        b_stmt = await self._generate(self.creator_b)
        await self._approve(a_stmt.id, version=1, snapshot={"account": "6222020200112233445"})

        page_a = await settlement.list_my_statements(self.db, user=self.creator_a, limit=None, cursor=None)
        self.assertEqual([item.id for item in page_a.items], [a_stmt.id])
        self.assertEqual(page_a.items[0].masked_account, "****3445")
        self.assertEqual(page_a.items[0].payout_state, PayoutStatus.CREATED.value)
        # 明细随本人结算单返回，且不含任何账户明文。
        self.assertEqual(sum(i.amount_fen for i in page_a.items[0].items), 10000)

        page_b = await settlement.list_my_statements(self.db, user=self.creator_b, limit=None, cursor=None)
        self.assertEqual([item.id for item in page_b.items], [b_stmt.id])
        self.assertIsNone(page_b.items[0].masked_account)

    # --- 确认与异议：版本校验、异议冻结 ---------------------------------------
    async def test_confirm_and_dispute_flow(self):
        self.enable()
        _, _, allocation = await self._accrued_allocation(self.creator_a, "100.00")
        statement = await self._generate(self.creator_a)

        with self.assertRaises(Conflict) as ctx:
            await settlement.confirm_statement(
                self.db,
                user=self.creator_a,
                statement_id=statement.id,
                payload=StatementConfirmIn(expected_version=99),
                request_id="t",
            )
        self.assertEqual(ctx.exception.code, "VERSION_CONFLICT")

        confirmed = await settlement.confirm_statement(
            self.db,
            user=self.creator_a,
            statement_id=statement.id,
            payload=StatementConfirmIn(expected_version=1),
            request_id="t",
        )
        self.assertEqual(confirmed.state, SettlementStatementState.CONFIRMED.value)

        disputed = await settlement.dispute_statement(
            self.db,
            user=self.creator_a,
            statement_id=statement.id,
            payload=StatementDisputeIn(
                expected_version=1,
                reason="金额有异议",
                items=[DisputeItemIn(allocation_id=allocation.id, amount_fen=10000)],
            ),
            request_id="t",
        )
        self.assertEqual(disputed.state, SettlementStatementState.DISPUTED.value)
        # 异议冻结争议金额：可结算净额降为 0，且已争议的结算单不可批准。
        self.assertEqual(await commerce.settleable_balance(self.db, self.creator_a.id, "CNY"), 0)
        with self.assertRaises(Conflict) as ctx:
            await self._approve(statement.id, version=1)
        self.assertEqual(ctx.exception.code, "STATEMENT_NOT_APPROVABLE")

    # --- 账务更正：关联原记录，不提供直接改余额 -------------------------------
    async def test_ledger_correction_links_original(self):
        self.enable()
        _, _, _ = await self._accrued_allocation(self.creator_a, "100.00")
        original = (await self.db.execute(select(LedgerJournal))).scalars().first()
        self.assertIsNotNone(original)

        correction = await settlement.record_ledger_correction(
            self.db,
            actor=self.admin,
            payload=LedgerCorrectionIn(original_journal_id=original.id, reason="更正原计提"),
            request_id="t",
        )
        self.assertEqual(correction.original_journal_id, original.id)
        self.assertEqual(correction.reversed_fen, 10000)
        new_journal = await self.db.get(LedgerJournal, correction.journal_id)
        self.assertEqual(new_journal.reverses_id, original.id)
        # 再次更正同一原记录 → 冲突（不重复更正）。
        with self.assertRaises(Conflict) as ctx:
            await settlement.record_ledger_correction(
                self.db,
                actor=self.admin,
                payload=LedgerCorrectionIn(original_journal_id=original.id, reason="再来一次"),
                request_id="t",
            )
        self.assertEqual(ctx.exception.code, "CORRECTION_ALREADY_EXISTS")


if __name__ == "__main__":
    unittest.main()
