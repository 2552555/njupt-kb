"""Beta15 授权与定价闭环（隔离 SQLite；不宣称生产并发结论）。

覆盖用户权责体系 §6—§8、§14.1/§14.4、§15.3、§16.2/§16.3、§19 T07/T08/T10：
开关关闭时写接口抛 NotEnabled、价格仅一方确认/双方不同哈希不生效、双方同哈希才 confirmed、
平台不能替贡献者确认价格、生效协议被修改生成新版本且旧版本不变、creator_bps 无默认比例、
组件权重校验与缺许可时 assert_sellable 拒绝、/me/agreements 不返回他人协议。

本文件只验证本地可执行的业务逻辑；生产联调（PostgreSQL/Redis/MinIO）不在本机范围内。
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import select

from app.config import settings
from app.core.errors import Conflict, Forbidden, InvalidInput, NotEnabled
from app.modules.commerce.models import Product
from app.modules.identity.models import User
from app.modules.licensing import service as licensing
from app.modules.licensing.models import (
    AgreementVersion,
    LicenseAgreement,
    PriceProposal,
)
from app.modules.licensing.schemas import (
    AgreementAcceptanceIn,
    AgreementCreateIn,
    AgreementVersionCreateIn,
    ComponentIn,
    CompositionVersionCreateIn,
    PriceAcceptanceIn,
    PriceProposalCreateIn,
    RevenueRuleVersionCreateIn,
)
from app.modules.moderation.models import AuditLog

import test_content_workflow as workflow


@contextmanager
def commerce_enabled():
    """临时开启商业授权与定价（模拟门槛放行），用例结束后恢复。"""
    with (
        patch.object(settings, "COMMERCE_ENABLED", True),
        patch.object(settings, "COMMERCE_COMPLIANCE_GATE_CLOSED", False),
    ):
        yield


class Beta15LicensingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="licensing-admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.db.add(self.admin)
        await self.db.flush()
        self.product = Product(
            kind="single_resource",
            title="付费复习笔记",
            owner_id=self.owner.id,
            publication_status="published",
        )
        self.db.add(self.product)
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # --- 辅助 -----------------------------------------------------------------

    async def _agreement(self, *, contributor=None, terms_hash="terms-v1"):
        agreement = await licensing.create_agreement(
            self.db,
            actor=self.admin,
            payload=AgreementCreateIn(
                title="销售授权协议", contributor_party_id=(contributor or self.owner).id
            ),
            request_id="beta15",
        )
        version = await licensing.create_agreement_version(
            self.db,
            actor=self.admin,
            agreement_id=agreement.id,
            payload=AgreementVersionCreateIn(
                terms_hash=terms_hash, rights_scope={"rights": ["sale"]}, territory="CN"
            ),
            request_id="beta15",
        )
        return agreement, version

    async def _activate(self, version, terms_hash, *, contributor=None):
        await licensing.accept_agreement(
            self.db,
            user=contributor or self.owner,
            version_id=version.id,
            payload=AgreementAcceptanceIn(accepted_hash=terms_hash),
            request_id="beta15",
        )
        await licensing.accept_agreement(
            self.db,
            user=self.admin,
            version_id=version.id,
            payload=AgreementAcceptanceIn(accepted_hash=terms_hash),
            request_id="beta15",
        )

    async def _confirmed_price(self, *, suggested_fen=1000, agreement_version_id=None):
        proposal = await licensing.create_price_proposal(
            self.db,
            user=self.owner,
            product_id=self.product.id,
            payload=PriceProposalCreateIn(
                suggested_fen=suggested_fen, agreement_version_id=agreement_version_id
            ),
            request_id="beta15",
        )
        await licensing.accept_price_proposal(
            self.db,
            user=self.owner,
            proposal_id=proposal.id,
            payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
            request_id="beta15",
        )
        return await licensing.accept_price_proposal(
            self.db,
            user=self.admin,
            proposal_id=proposal.id,
            payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
            request_id="beta15",
        )

    # --- 开关关闭：写接口抛 NotEnabled（不返回假成功）--------------------------

    async def test_write_endpoints_raise_not_enabled_when_gate_closed(self):
        self.assertFalse(settings.COMMERCE_ENABLED)
        self.assertTrue(settings.COMMERCE_COMPLIANCE_GATE_CLOSED)
        with self.assertRaises(NotEnabled):
            await licensing.create_agreement(
                self.db,
                actor=self.admin,
                payload=AgreementCreateIn(title="x", contributor_party_id=self.owner.id),
                request_id="beta15",
            )
        with self.assertRaises(NotEnabled):
            await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=1000),
                request_id="beta15",
            )
        with self.assertRaises(NotEnabled):
            await licensing.publish_revenue_rule(
                self.db,
                actor=self.admin,
                payload=RevenueRuleVersionCreateIn(
                    base_definition="实收含税价", creator_bps=8000,
                    rounding_policy="floor", maturity_policy="t+7",
                ),
                request_id="beta15",
            )

    async def test_api_write_returns_503_feature_not_enabled(self):
        import httpx

        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: self.admin
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/admin/agreements",
                    json={"title": "x", "contributor_party_id": str(self.owner.id)},
                    # CSRF 双提交：会话下写接口需要 Cookie 与头一致。
                    cookies={settings.CSRF_COOKIE_NAME: "t"},
                    headers={settings.CSRF_HEADER_NAME: "t"},
                )
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["error"]["code"], "FEATURE_NOT_ENABLED")
        finally:
            app.dependency_overrides.clear()

    # --- 价格：仅一方/不同哈希不生效，双方同哈希 confirmed（§19 T07）----------

    async def test_price_requires_both_parties_same_hash(self):
        with commerce_enabled():
            proposal = await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=1000),
                request_id="beta15",
            )
            self.assertEqual(proposal.state.value, "proposed")

            # 仅贡献者确认：不生效。
            after_one = await licensing.accept_price_proposal(
                self.db,
                user=self.owner,
                proposal_id=proposal.id,
                payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
                request_id="beta15",
            )
            self.assertNotEqual(after_one.state.value, "confirmed")
            stored = await self.db.get(PriceProposal, proposal.id)
            await self.db.refresh(stored)
            self.assertNotEqual(stored.state, "confirmed")
            self.assertIsNone(stored.final_fen)

            # 平台确认不同哈希：不生效（Conflict）。
            with self.assertRaises(Conflict):
                await licensing.accept_price_proposal(
                    self.db,
                    user=self.admin,
                    proposal_id=proposal.id,
                    payload=PriceAcceptanceIn(accepted_hash="not-the-server-hash"),
                    request_id="beta15",
                )
            await self.db.refresh(stored)
            self.assertNotEqual(stored.state, "confirmed")

            # 双方确认同一哈希：confirmed 并冻结最终金额。
            confirmed = await licensing.accept_price_proposal(
                self.db,
                user=self.admin,
                proposal_id=proposal.id,
                payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
                request_id="beta15",
            )
            self.assertEqual(confirmed.state.value, "confirmed")
            self.assertEqual(confirmed.final_fen, 1000)

    async def test_platform_cannot_confirm_price_for_contributor(self):
        with commerce_enabled():
            proposal = await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=500),
                request_id="beta15",
            )
            # 操作者即贡献者主体：平台接口必须拒绝，不能代表贡献者确认。
            with self.assertRaises(Forbidden):
                await licensing.admin_accept_price_proposal(
                    self.db,
                    actor=self.owner,
                    proposal_id=proposal.id,
                    payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
                    request_id="beta15",
                )

    async def test_price_proposal_change_creates_new_version(self):
        with commerce_enabled():
            first = await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=1000),
                request_id="beta15",
            )
            second = await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=1200),
                request_id="beta15",
            )
            self.assertEqual(second.version_no, first.version_no + 1)
            stored_first = await self.db.get(PriceProposal, first.id)
            await self.db.refresh(stored_first)
            # 旧版金额不变（不覆盖），只按生命周期置 SUPERSEDED。
            self.assertEqual(stored_first.suggested_fen, 1000)
            self.assertEqual(stored_first.state, "superseded")

    # --- 协议：生效后修改生成新版本，旧版本不变（§19 T08）---------------------

    async def test_agreement_change_creates_new_version_and_keeps_old(self):
        with commerce_enabled():
            agreement, v1 = await self._agreement(terms_hash="h1")
            await self._activate(v1, "h1")

            stored_v1 = await self.db.get(AgreementVersion, v1.id)
            await self.db.refresh(stored_v1)
            self.assertEqual(stored_v1.state, "active")

            v2 = await licensing.create_agreement_version(
                self.db,
                actor=self.admin,
                agreement_id=agreement.id,
                payload=AgreementVersionCreateIn(terms_hash="h2"),
                request_id="beta15",
            )
            self.assertEqual(v2.version_no, 2)
            self.assertEqual(v2.state.value, "awaiting_acceptance")

            # 旧版本保持：状态与哈希都不变。
            await self.db.refresh(stored_v1)
            self.assertEqual(stored_v1.state, "active")
            self.assertEqual(stored_v1.terms_hash, "h1")

            # 确认留痕（审计）。
            audit = await self.db.scalar(
                select(AuditLog.id)
                .where(AuditLog.action == "licensing.agreement.accepted")
                .limit(1)
            )
            self.assertIsNotNone(audit)

    async def test_agreement_acceptance_rejects_wrong_hash(self):
        with commerce_enabled():
            _agreement, version = await self._agreement(terms_hash="h1")
            with self.assertRaises(Conflict):
                await licensing.accept_agreement(
                    self.db,
                    user=self.owner,
                    version_id=version.id,
                    payload=AgreementAcceptanceIn(accepted_hash="wrong"),
                    request_id="beta15",
                )

    # --- 收益规则：creator_bps 未显式给出不产生默认比例 -----------------------

    async def test_revenue_rule_requires_explicit_creator_bps(self):
        # settings.REVENUE_DEFAULT_CREATOR_BPS=0 表示「未配置」，不得据此产生默认比例。
        self.assertEqual(settings.REVENUE_DEFAULT_CREATOR_BPS, 0)
        with commerce_enabled():
            with self.assertRaises(InvalidInput):
                await licensing.publish_revenue_rule(
                    self.db,
                    actor=self.admin,
                    payload=RevenueRuleVersionCreateIn(
                        base_definition="实收含税价",
                        rounding_policy="floor",
                        maturity_policy="t+7",
                    ),
                    request_id="beta15",
                )
            rule = await licensing.publish_revenue_rule(
                self.db,
                actor=self.admin,
                payload=RevenueRuleVersionCreateIn(
                    base_definition="实收含税价",
                    creator_bps=8000,
                    rounding_policy="floor",
                    maturity_policy="t+7",
                ),
                request_id="beta15",
            )
            self.assertEqual(rule.creator_bps, 8000)
            self.assertEqual(rule.version_no, 1)

    # --- 组件权重与可售性（§19 T10）------------------------------------------

    async def test_composition_weight_validation(self):
        with commerce_enabled():
            with self.assertRaises(InvalidInput):
                await licensing.freeze_composition(
                    self.db,
                    actor=self.admin,
                    payload=CompositionVersionCreateIn(
                        product_id=self.product.id,
                        components=[ComponentIn(weight=0), ComponentIn(weight=0)],
                    ),
                    request_id="beta15",
                )
            with self.assertRaises(InvalidInput):
                await licensing.freeze_composition(
                    self.db,
                    actor=self.admin,
                    payload=CompositionVersionCreateIn(
                        product_id=self.product.id,
                        components=[ComponentIn(weight=-1), ComponentIn(weight=1)],
                    ),
                    request_id="beta15",
                )

    async def test_assert_sellable_rejects_missing_component_license(self):
        with commerce_enabled():
            await self._confirmed_price()
            await licensing.publish_revenue_rule(
                self.db,
                actor=self.admin,
                payload=RevenueRuleVersionCreateIn(
                    base_definition="实收含税价",
                    creator_bps=8000,
                    rounding_policy="floor",
                    maturity_policy="t+7",
                ),
                request_id="beta15",
            )
            # 组件缺少销售许可：整个报价不得可售，无部分绕过。
            await licensing.freeze_composition(
                self.db,
                actor=self.admin,
                payload=CompositionVersionCreateIn(
                    product_id=self.product.id,
                    components=[ComponentIn(resource_version_id=self.version.id, weight=1)],
                ),
                request_id="beta15",
            )
            with self.assertRaises(Conflict):
                await licensing.assert_sellable(self.db, self.product.id)

    async def test_assert_sellable_passes_with_licensed_components(self):
        with commerce_enabled():
            _agreement, version = await self._agreement(terms_hash="h1")
            await self._activate(version, "h1")
            await self._confirmed_price()
            await licensing.publish_revenue_rule(
                self.db,
                actor=self.admin,
                payload=RevenueRuleVersionCreateIn(
                    base_definition="实收含税价",
                    creator_bps=8000,
                    rounding_policy="floor",
                    maturity_policy="t+7",
                ),
                request_id="beta15",
            )
            await licensing.freeze_composition(
                self.db,
                actor=self.admin,
                payload=CompositionVersionCreateIn(
                    product_id=self.product.id,
                    components=[
                        ComponentIn(
                            resource_version_id=self.version.id,
                            agreement_version_id=version.id,
                            weight=1,
                        )
                    ],
                ),
                request_id="beta15",
            )
            self.assertTrue(await licensing.assert_sellable(self.db, self.product.id))

    async def test_assert_sellable_rejects_without_confirmed_price(self):
        with commerce_enabled():
            await licensing.publish_revenue_rule(
                self.db,
                actor=self.admin,
                payload=RevenueRuleVersionCreateIn(
                    base_definition="实收含税价",
                    creator_bps=8000,
                    rounding_policy="floor",
                    maturity_policy="t+7",
                ),
                request_id="beta15",
            )
            with self.assertRaises(Conflict):
                await licensing.assert_sellable(self.db, self.product.id)

    # --- /me/agreements：本人协议隔离，读接口在开关关闭时仍可返回 -------------

    async def test_me_agreements_isolated_at_service_level(self):
        with commerce_enabled():
            agreement, _version = await self._agreement(terms_hash="h1")
            mine = await licensing.list_my_agreements(self.db, user=self.owner)
            self.assertEqual([a.id for a in mine], [agreement.id])
            self.assertEqual(await licensing.list_my_agreements(self.db, user=self.other), [])

    async def test_me_agreements_readable_when_gate_closed_and_isolated(self):
        import httpx

        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        # 开关关闭时直接写入本机虚构协议（读接口不依赖写开关）。
        agreement = LicenseAgreement(
            platform_party_id=licensing.PLATFORM_PARTY_ID,
            contributor_party_id=self.owner.id,
            title="我的授权协议",
            state="active",
        )
        self.db.add(agreement)
        await self.db.flush()
        self.db.add(
            AgreementVersion(
                agreement_id=agreement.id,
                version_no=1,
                terms_hash="h1",
                rights_scope={"rights": ["sale"]},
                state="active",
            )
        )
        await self.db.commit()

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                app.dependency_overrides[get_current_user] = lambda: self.owner
                mine = await client.get("/api/v1/me/agreements")
                self.assertEqual(mine.status_code, 200, mine.text)
                body = mine.json()
                self.assertEqual([item["id"] for item in body], [str(agreement.id)])
                self.assertEqual(len(body[0]["versions"]), 1)
                # 不返回任何资金字段。
                self.assertNotIn("suggested_fen", body[0])
                self.assertNotIn("final_fen", body[0])

                app.dependency_overrides[get_current_user] = lambda: self.other
                other = await client.get("/api/v1/me/agreements")
                self.assertEqual(other.status_code, 200, other.text)
                self.assertEqual(other.json(), [])
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
