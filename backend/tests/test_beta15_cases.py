"""Beta15 案件与侵权通知闭环（隔离 SQLite；不宣称生产并发结论）。

覆盖用户权责体系 §13、§14.1、§15.5、§16.2/16.3、§19 T26—T28：
他人案件不可读且不泄露存在性、本人进度分层返回、侵权通知免购买且限频、
反通知仅被通知方可提交、UNPUBLISH 措施生效与解除不自动恢复公开、
措施列表/审计/事件正确、active_measures 只返回生效中的措施。

本文件只验证本地可执行的业务逻辑，生产联调（PostgreSQL/Redis/MinIO）不在本机范围内。
"""

import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.config import settings
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound, RateLimited
from app.core.ratelimit import reset_rate_limit
from app.modules.access.models import AccessPolicy
from app.modules.cases import service as cases
from app.modules.cases.models import Case, CaseEvent, CaseMeasure, RightsNotice
from app.modules.cases.schemas import (
    CaseCreateIn,
    CaseEventIn,
    CounterStatementIn,
    MeasureCreateIn,
    RightsNoticeCreateIn,
)
from app.modules.content.models import Resource
from app.modules.identity.models import User
from app.modules.moderation.models import AuditLog
from app.modules.notification.models import DomainEvent

import test_content_workflow as workflow


class Beta15CasesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 案件措施要求资源已发布（UNPUBLISH 才有可观察效果）。
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.db.add(self.admin)
        await self.db.flush()
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # --- 辅助 -----------------------------------------------------------------

    async def _open_case(self, *, user=None, case_type="general_appeal"):
        return await cases.create_case(
            self.db,
            applicant=user or self.owner,
            payload=CaseCreateIn(
                case_type=case_type,
                subject_type="resource",
                subject_id=self.resource.id,
                title="测试案件",
                description="我的理由",
            ),
            request_id="beta15",
        )

    async def _open_rights_notice(self, *, claimant=None):
        return await cases.create_rights_notice(
            self.db,
            claimant=claimant or self.other,
            payload=RightsNoticeCreateIn(
                claimant_name="权利人甲",
                claimant_contact="owner@example.test",
                rights_basis_ref="copyright-reg-001",
                target_type="resource",
                target_id=self.resource.id,
                content_ref="https://example.test/r/1",
                requested_action="删除并停止传播",
                statement_version="truthfulness-v1",
                evidence_ref="evidence-bundle-1",
                truthfulness_declared=True,
            ),
            request_id="beta15",
        )

    # --- 案件可主张对象与专用入口 ---------------------------------------------

    async def test_unrelated_user_cannot_open_case_on_resource(self):
        with self.assertRaises(Forbidden):
            await self._open_case(user=self.other)

    async def test_rights_notice_type_rejected_on_generic_endpoint(self):
        with self.assertRaises(InvalidInput):
            await cases.create_case(
                self.db,
                applicant=self.owner,
                payload=CaseCreateIn(
                    case_type="rights_notice",
                    subject_type="resource",
                    subject_id=self.resource.id,
                    title="走错入口",
                ),
                request_id="beta15",
            )

    # --- 他人案件不可读、本人进度分层 -----------------------------------------

    async def test_other_user_cannot_read_my_case(self):
        case = await self._open_case()
        with self.assertRaises(NotFound):
            await cases.get_my_case(self.db, user=self.other, case_id=case.id)

    async def test_handler_can_read_but_other_user_gets_404_via_api(self):
        import httpx

        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        case = await self._open_case()

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                path = f"/api/v1/me/cases/{case.id}"
                app.dependency_overrides[get_current_user] = lambda: self.other
                denied = await client.get(path)
                # 非本人且非处理人：统一 404，不泄露存在性。
                self.assertEqual(denied.status_code, 404, denied.text)

                app.dependency_overrides[get_current_user] = lambda: self.admin
                allowed = await client.get(path)
                self.assertEqual(allowed.status_code, 200, allowed.text)
        finally:
            app.dependency_overrides.clear()

    async def test_my_case_progress_hides_internal_fields(self):
        import httpx

        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        case = await self._open_case()
        # 处理人转送并留下内部备注、证据与处理人身份。
        await cases.append_case_event(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=CaseEventIn(
                event_type="forwarded",
                reason="已向被通知方转送",
                note="内部备注：待观察",
                evidence_ref="internal-evidence",
                served_at=case.created_at,
                deadline_at=case.created_at + timedelta(days=15),
            ),
            request_id="beta15",
        )
        await cases.append_case_event(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=CaseEventIn(event_type="decided", reason="结论：不构成违规"),
            request_id="beta15",
        )

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                path = f"/api/v1/me/cases/{case.id}"
                app.dependency_overrides[get_current_user] = lambda: self.owner
                owner_view = (await client.get(path)).json()
                # 本人可见对外结论，不可见处理人身份。
                self.assertEqual(owner_view["decision_reason"], "结论：不构成违规")
                self.assertNotIn("handler_id", owner_view)
                for event in owner_view["events"]:
                    self.assertNotIn("note", event)
                    self.assertNotIn("evidence_ref", event)
                    self.assertNotIn("actor_id", event)

                app.dependency_overrides[get_current_user] = lambda: self.admin
                handler_view = (await client.get(path)).json()
                forwarded = next(
                    e for e in handler_view["events"] if e["event_type"] == "forwarded"
                )
                self.assertEqual(forwarded["note"], "内部备注：待观察")
                self.assertEqual(forwarded["evidence_ref"], "internal-evidence")
                self.assertIsNotNone(forwarded["actor_id"])
        finally:
            app.dependency_overrides.clear()

    # --- 侵权通知：免购买 + 限频 ----------------------------------------------

    async def test_rights_notice_needs_no_purchase_and_rate_limited(self):
        # 申请人不是资源所有者也可提交（无需购买/无需权属关系）。
        receipt = await self._open_rights_notice(claimant=self.other)
        self.assertEqual(receipt.status.value, "received")

        await reset_rate_limit(f"rights-notice:{self.owner.id}")
        with patch.object(settings, "RIGHTS_NOTICE_RATE_LIMIT", 1):
            await self._open_rights_notice(claimant=self.owner)
            with self.assertRaises(RateLimited):
                await self._open_rights_notice(claimant=self.owner)

    async def test_rights_notice_response_omits_evidence_and_identity(self):
        receipt = await self._open_rights_notice(claimant=self.other)
        payload = receipt.model_dump()
        self.assertNotIn("evidence_ref", payload)
        self.assertNotIn("claimant_contact", payload)
        self.assertNotIn("claimant_name", payload)

    # --- 反通知：仅被通知方 ---------------------------------------------------

    async def test_counter_statement_only_notified_party(self):
        receipt = await self._open_rights_notice(claimant=self.other)

        # 申请人（权利人，非被通知方）不能提交反通知。
        with self.assertRaises(Forbidden):
            await cases.create_counter_statement(
                self.db,
                user=self.other,
                notice_id=receipt.notice_id,
                payload=CounterStatementIn(statement_text="我声明不侵权"),
                request_id="beta15",
            )

        # 被通知方（资源所有者）可以提交。
        statement = await cases.create_counter_statement(
            self.db,
            user=self.owner,
            notice_id=receipt.notice_id,
            payload=CounterStatementIn(statement_text="我不存在侵权", evidence_ref="counter-1"),
            request_id="beta15",
        )
        self.assertEqual(statement.rights_notice_id, receipt.notice_id)

        notice = await self.db.get(RightsNotice, receipt.notice_id)
        self.assertEqual(notice.status, "counter_received")

        event = await self.db.scalar(
            select(CaseEvent).where(
                CaseEvent.case_id == receipt.case_id,
                CaseEvent.event_type == "counter_notice",
            )
        )
        self.assertIsNotNone(event.deadline_at)
        self.assertEqual(
            (event.deadline_at - event.served_at).days,
            settings.RIGHTS_NOTICE_COUNTER_DEADLINE_DAYS,
        )

    # --- 措施：UNPUBLISH 生效与解除不自动恢复 ---------------------------------

    async def test_unpublish_measure_withdraws_and_release_does_not_republish(self):
        case = await self._open_case()
        measure, applied = await cases.take_measure(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=MeasureCreateIn(
                measure="unpublish",
                target_type="resource",
                target_id=self.resource.id,
                reason="侵权初步证据成立",
            ),
            request_id="beta15",
        )
        self.assertTrue(applied)
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.publication_status, "withdrawn")

        released = await cases.release_measure(
            self.db,
            case_id=case.id,
            measure_id=measure.id,
            actor=self.admin,
            request_id="beta15",
        )
        self.assertEqual(released.status, "released")
        await self.db.refresh(self.resource)
        # 解除措施不机械自动重新上架（§13.4）。
        self.assertEqual(self.resource.publication_status, "withdrawn")

    async def test_release_measure_twice_conflicts(self):
        case = await self._open_case()
        measure, _ = await cases.take_measure(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=MeasureCreateIn(
                measure="unpublish",
                target_type="resource",
                target_id=self.resource.id,
                reason="下架",
            ),
            request_id="beta15",
        )
        await cases.release_measure(
            self.db, case_id=case.id, measure_id=measure.id, actor=self.admin, request_id="beta15"
        )
        with self.assertRaises(Conflict):
            await cases.release_measure(
                self.db, case_id=case.id, measure_id=measure.id, actor=self.admin, request_id="beta15"
            )

    # --- 措施列表 / 审计 / 事件 / active_measures -----------------------------

    async def test_measure_records_audit_event_and_active_measures(self):
        case = await self._open_case()
        measure, applied = await cases.take_measure(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=MeasureCreateIn(
                measure="unpublish",
                target_type="resource",
                target_id=self.resource.id,
                reason="下架",
            ),
            request_id="beta15",
        )

        # 事件：MEASURE_TAKEN 追加写。
        taken = await self.db.scalar(
            select(CaseEvent).where(
                CaseEvent.case_id == case.id,
                CaseEvent.event_type == "measure_taken",
            )
        )
        self.assertIsNotNone(taken)

        # 审计：采取措施留痕。
        audit_count = await self.db.scalar(
            select(AuditLog.id).where(AuditLog.action == "case.measure.taken").limit(1)
        )
        self.assertIsNotNone(audit_count)

        # active_measures 只返回 active。
        active = await cases.active_measures(self.db, "resource", self.resource.id)
        self.assertEqual([m.id for m in active], [measure.id])

        await cases.release_measure(
            self.db, case_id=case.id, measure_id=measure.id, actor=self.admin, request_id="beta15"
        )
        self.assertEqual(await cases.active_measures(self.db, "resource", self.resource.id), [])

        released_event = await self.db.scalar(
            select(CaseEvent).where(
                CaseEvent.case_id == case.id,
                CaseEvent.event_type == "measure_released",
            )
        )
        self.assertIsNotNone(released_event)

    async def test_block_download_revokes_active_grants_only(self):
        from datetime import datetime, timezone

        from app.modules.access.models import DownloadGrant
        from app.modules.content.models import ResourceFile

        file = ResourceFile(
            uploader_id=self.owner.id, storage_key="k", original_name="a.pdf"
        )
        self.db.add(file)
        await self.db.flush()

        def grant(key, status):
            return DownloadGrant(
                user_id=self.other.id,
                resource_id=self.resource.id,
                version_id=self.version.id,
                file_id=file.id,
                idempotency_key=key,
                request_hash="h",
                status=status,
                valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        active = grant("k-active", "issued")
        consumed = grant("k-consumed", "consumed")
        expired = grant("k-expired", "expired")
        self.db.add_all([active, consumed, expired])
        await self.db.flush()

        case = await self._open_case()
        await cases.take_measure(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=MeasureCreateIn(
                measure="block_download",
                target_type="resource",
                target_id=self.resource.id,
                reason="阻断下载",
            ),
            request_id="beta15",
        )
        # 只改活跃授权的状态、不删记录（沿用 beta13 语义）。
        self.assertEqual((await self.db.get(DownloadGrant, active.id)).status, "revoked")
        self.assertEqual((await self.db.get(DownloadGrant, consumed.id)).status, "revoked")
        self.assertEqual((await self.db.get(DownloadGrant, expired.id)).status, "expired")

    async def test_commerce_measure_records_only_without_funds(self):
        case = await self._open_case()
        measure, applied = await cases.take_measure(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=MeasureCreateIn(
                measure="block_sales",
                target_type="resource",
                target_id=self.resource.id,
                reason="停止新销售",
            ),
            request_id="beta15",
        )
        # 商业化关闭：只记录措施行，不改动任何资金，响应标注未启用。
        self.assertFalse(applied)
        self.assertEqual(measure.status, "active")
        self.assertFalse(cases.measure_out(measure).effect_applied)
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.publication_status, "published")

    # --- 事件 / 领域事件 ------------------------------------------------------

    async def test_case_created_emits_event_and_audit(self):
        case = await self._open_case()
        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "case.created",
                DomainEvent.subject_id == case.id,
            )
        )
        self.assertIsNotNone(event)
        audit = await self.db.scalar(
            select(AuditLog.id).where(
                AuditLog.action == "case.created", AuditLog.object_id == case.id
            )
        )
        self.assertIsNotNone(audit)

    async def test_decided_event_sets_state_and_external_conclusion(self):
        case = await self._open_case()
        await cases.append_case_event(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=CaseEventIn(event_type="decided", reason="维持原决定"),
            request_id="beta15",
        )
        stored = await self.db.get(Case, case.id)
        self.assertEqual(stored.state, "decided")
        self.assertEqual(stored.decision_reason, "维持原决定")
        self.assertIsNotNone(stored.decided_at)
        decided = await self.db.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "case.decided")
        )
        self.assertIsNotNone(decided)

    async def test_note_event_does_not_change_state(self):
        case = await self._open_case()
        await cases.append_case_event(
            self.db,
            case_id=case.id,
            actor=self.admin,
            payload=CaseEventIn(event_type="note", reason="补充说明"),
            request_id="beta15",
        )
        stored = await self.db.get(Case, case.id)
        self.assertEqual(stored.state, "open")

    async def test_open_cap_blocks_new_case(self):
        with patch.object(settings, "CASE_OPEN_CAP", 1), patch.object(
            settings, "CASE_RATE_LIMIT", 100
        ):
            await self._open_case()
            with self.assertRaises(Conflict):
                await self._open_case()


if __name__ == "__main__":
    unittest.main()
