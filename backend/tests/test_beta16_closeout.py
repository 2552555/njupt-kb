"""Beta16 收口 beta15 遗留的四项边界（隔离 SQLite；不宣称生产并发结论）。

覆盖：
1. `withdraw_resource` 与 `create_upload_session` 追加资源级分配判定（不放宽审核权限）；
2. 后台案件事件联动 `rights_notices.status`（只前进不回退、未关联时静默跳过）；
3. licensing 写 `agreement.accepted` / `price.proposed` / `price.confirmed` /
   `price.rejected` 域事件，payload 不含哈希与身份信息；
4. 事件重试不重复投递。

说明：`resource_assignments` 只有 `effective_at`、没有 `valid_until` 列，本轮不新增迁移，
因此「分配失效」用「已撤销」与「尚未生效」两种可表达的情形验证（见对应用例注释）。
"""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, select

from app.config import settings
from app.core.enums import AssignmentCapability, DomainEventType, RightsNoticeStatus
from app.core.errors import NotFound
from app.core.storage import PresignedUpload
from app.modules.access.models import AccessPolicy
from app.modules.assignment import service as assignment
from app.modules.assignment.schemas import ResourceAssignmentCreate
from app.modules.cases import service as cases
from app.modules.cases.models import RightsNotice
from app.modules.cases.schemas import CaseCreateIn, CaseEventIn, RightsNoticeCreateIn
from app.modules.commerce.models import Product
from app.modules.content import service as content
from app.modules.content.models import UploadSession
from app.modules.content.schemas import UploadCreateIn, WithdrawIn
from app.modules.identity.models import User
from app.modules.licensing import service as licensing
from app.modules.licensing.models import PriceProposal
from app.modules.licensing.schemas import (
    AgreementAcceptanceIn,
    AgreementCreateIn,
    AgreementVersionCreateIn,
    PriceAcceptanceIn,
    PriceProposalCreateIn,
)
from app.modules.moderation.models import AuditLog
from app.modules.notification import service as notifications
from app.modules.notification.models import DomainEvent, Notification

import test_content_workflow as workflow


@contextmanager
def commerce_enabled():
    """临时开启商业授权与定价（模拟门槛放行），用例结束后恢复。"""
    with (
        patch.object(settings, "COMMERCE_ENABLED", True),
        patch.object(settings, "COMMERCE_COMPLIANCE_GATE_CLOSED", False),
    ):
        yield


class Beta16CloseoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 撤回与案件措施都要求资源已发布。
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.admin = User(
            email="beta16-admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.manager = User(
            email="beta16-manager@example.test",
            password_hash="unused",
            role="contributor",
            status="active",
        )
        self.db.add_all([self.admin, self.manager])
        await self.db.flush()
        self.product = Product(
            kind="single_resource",
            title="beta16 定价商品",
            owner_id=self.owner.id,
            publication_status="published",
        )
        self.db.add(self.product)
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # --- 辅助 -----------------------------------------------------------------

    async def _assign(self, capability=AssignmentCapability.MAINTAIN, **kwargs):
        row = await assignment.create_resource_assignment(
            self.db,
            actor=self.admin,
            payload=ResourceAssignmentCreate(
                user_id=self.manager.id,
                resource_id=self.resource.id,
                capability=capability,
                reason="beta16 测试分配",
                **kwargs,
            ),
            request_id="beta16",
        )
        await self.db.flush()
        return row

    async def _withdraw(self, user):
        return await content.withdraw_resource(
            self.db,
            user=user,
            resource_id=self.resource.id,
            payload=WithdrawIn(reason="beta16 测试撤回"),
            request_id="beta16",
        )

    async def _upload(self, user):
        async def fake_presign(storage_key, content_type, *, bucket=None):
            return PresignedUpload(
                url="http://test.invalid/upload",
                storage_key=storage_key,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )

        with patch.object(content.storage, "presign_upload", fake_presign):
            return await content.create_upload_session(
                self.db,
                user=user,
                payload=UploadCreateIn(
                    original_name="notes.pdf",
                    declared_mime="application/pdf",
                    expected_size=1024,
                    resource_id=self.resource.id,
                ),
            )

    async def _open_rights_notice(self):
        return await cases.create_rights_notice(
            self.db,
            claimant=self.other,
            payload=RightsNoticeCreateIn(
                claimant_name="权利人乙",
                claimant_contact="other@example.test",
                rights_basis_ref="copyright-reg-002",
                target_type="resource",
                target_id=self.resource.id,
                content_ref="https://example.test/r/2",
                requested_action="删除并停止传播",
                statement_version="truthfulness-v1",
                evidence_ref="evidence-bundle-2",
                truthfulness_declared=True,
            ),
            request_id="beta16",
        )

    async def _append(self, case_id, event_type, reason="beta16 处理"):
        return await cases.append_case_event(
            self.db,
            case_id=case_id,
            actor=self.admin,
            payload=CaseEventIn(event_type=event_type, reason=reason),
            request_id="beta16",
        )

    async def _notice_status(self, notice_id):
        notice = await self.db.get(RightsNotice, notice_id)
        await self.db.refresh(notice)
        return notice.status

    async def _agreement_version(self, terms_hash="h1"):
        agreement = await licensing.create_agreement(
            self.db,
            actor=self.admin,
            payload=AgreementCreateIn(
                title="beta16 销售授权协议", contributor_party_id=self.owner.id
            ),
            request_id="beta16",
        )
        return agreement, await licensing.create_agreement_version(
            self.db,
            actor=self.admin,
            agreement_id=agreement.id,
            payload=AgreementVersionCreateIn(terms_hash=terms_hash),
            request_id="beta16",
        )

    async def _events(self, event_type):
        return list(
            (
                await self.db.execute(
                    select(DomainEvent).where(DomainEvent.event_type == event_type)
                )
            )
            .scalars()
            .all()
        )

    # --- 1/2/3：撤回与上传会话追加分配判定 ------------------------------------

    async def test_manage_assignment_can_withdraw_resource(self):
        await self._assign(AssignmentCapability.MANAGE)

        out = await self._withdraw(self.manager)

        self.assertEqual(out.publication_status.value, "withdrawn")
        # 审计 actor 记实际操作人，不记 owner。
        actor_id = await self.db.scalar(
            select(AuditLog.actor_id).where(AuditLog.action == "resource.withdrawn").limit(1)
        )
        self.assertEqual(actor_id, self.manager.id)

    async def test_unassigned_user_cannot_withdraw_resource(self):
        with self.assertRaises(NotFound):
            await self._withdraw(self.other)
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.publication_status, "published")

    async def test_review_assignment_cannot_withdraw_resource(self):
        # REVIEW 只够审核，不构成撤回所需的 MANAGE：证明分配判定未放宽既有权限。
        await self._assign(AssignmentCapability.REVIEW)

        with self.assertRaises(NotFound):
            await self._withdraw(self.manager)
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.publication_status, "published")

    async def test_maintain_assignment_can_create_upload_session(self):
        await self._assign(AssignmentCapability.MAINTAIN)

        out = await self._upload(self.manager)

        session = await self.db.get(UploadSession, out.id)
        self.assertIsNotNone(session)
        # 会话归属仍记实际创建人，不改资源归属。
        self.assertEqual(session.uploader_id, self.manager.id)
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.owner_id, self.owner.id)

    async def test_unassigned_user_cannot_create_upload_session(self):
        with self.assertRaises(NotFound):
            await self._upload(self.other)

    async def test_revoked_assignment_cannot_withdraw_resource(self):
        row = await self._assign(AssignmentCapability.MANAGE)
        await assignment.revoke_resource_assignment(
            self.db,
            actor=self.admin,
            assignment_id=row.id,
            reason="结束指派",
            request_id="beta16",
        )
        await self.db.flush()

        with self.assertRaises(NotFound):
            await self._withdraw(self.manager)

    async def test_not_yet_effective_assignment_cannot_withdraw_resource(self):
        # `resource_assignments` 无 `valid_until` 列（本轮不加迁移），
        # 失效由 `revoked_at` 与「尚未生效的 effective_at」表达；此处验证后者。
        await self._assign(
            AssignmentCapability.MANAGE,
            effective_at=datetime.now(timezone.utc) + timedelta(days=1),
        )

        with self.assertRaises(NotFound):
            await self._withdraw(self.manager)

    # --- 4：案件事件联动侵权通知状态 ------------------------------------------

    async def test_case_forwarded_advances_rights_notice(self):
        receipt = await self._open_rights_notice()
        self.assertEqual(await self._notice_status(receipt.notice_id), "received")

        await self._append(receipt.case_id, "forwarded")

        self.assertEqual(
            await self._notice_status(receipt.notice_id), RightsNoticeStatus.FORWARDED.value
        )

    async def test_case_decided_sets_notice_terminal(self):
        receipt = await self._open_rights_notice()
        await self._append(receipt.case_id, "forwarded")

        await self._append(receipt.case_id, "decided", reason="结论：已采取必要措施")

        self.assertEqual(
            await self._notice_status(receipt.notice_id), RightsNoticeStatus.MEASURED.value
        )

    async def test_notice_terminal_state_not_regressed(self):
        receipt = await self._open_rights_notice()
        await self._append(receipt.case_id, "decided", reason="结论：已采取必要措施")
        decided = await self._notice_status(receipt.notice_id)

        # 终态后再次推进：不覆盖、不回退、不报错（幂等）。
        await self._append(receipt.case_id, "forwarded", reason="补充转送记录")

        self.assertEqual(await self._notice_status(receipt.notice_id), decided)

    async def test_case_without_notice_skips_silently(self):
        case = await cases.create_case(
            self.db,
            applicant=self.owner,
            payload=CaseCreateIn(
                case_type="general_appeal",
                subject_type="resource",
                subject_id=self.resource.id,
                title="未关联通知的案件",
                description="仅验证联动静默跳过",
            ),
            request_id="beta16",
        )

        # 不抛异常即通过；且该案件没有生成任何通知行。
        await self._append(case.id, "forwarded")
        count = await self.db.scalar(
            select(func.count()).select_from(RightsNotice).where(RightsNotice.case_id == case.id)
        )
        self.assertEqual(count, 0)

    # --- 5：licensing 域事件 ---------------------------------------------------

    async def test_agreement_acceptance_emits_event(self):
        with commerce_enabled():
            _agreement, version = await self._agreement_version("h-accept")
            await licensing.accept_agreement(
                self.db,
                user=self.owner,
                version_id=version.id,
                payload=AgreementAcceptanceIn(accepted_hash="h-accept"),
                request_id="beta16",
            )

        events = await self._events(DomainEventType.AGREEMENT_ACCEPTED.value)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.subject_id, version.id)
        self.assertEqual(event.payload["party_id"], str(self.owner.id))
        self.assertFalse(event.payload["both_accepted"])
        # 哈希与身份信息不进事件。
        self.assertNotIn("accepted_hash", event.payload)
        self.assertNotIn("claimant_contact", event.payload)

    async def test_price_events_emitted_without_sensitive_payload(self):
        with commerce_enabled():
            proposal = await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=1500),
                request_id="beta16",
            )
            await licensing.accept_price_proposal(
                self.db,
                user=self.owner,
                proposal_id=proposal.id,
                payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
                request_id="beta16",
            )
            await licensing.accept_price_proposal(
                self.db,
                user=self.admin,
                proposal_id=proposal.id,
                payload=PriceAcceptanceIn(accepted_hash=proposal.proposal_hash),
                request_id="beta16",
            )
            rejected = await licensing.create_price_proposal(
                self.db,
                user=self.owner,
                product_id=self.product.id,
                payload=PriceProposalCreateIn(suggested_fen=2500),
                request_id="beta16",
            )
            await licensing.reject_price_proposal(
                self.db,
                user=self.admin,
                proposal_id=rejected.id,
                reason="价格高于同类资料",
                request_id="beta16",
            )

        proposed = await self._events(DomainEventType.PRICE_PROPOSED.value)
        confirmed = await self._events(DomainEventType.PRICE_CONFIRMED.value)
        rejected_events = await self._events(DomainEventType.PRICE_REJECTED.value)
        self.assertEqual(len(proposed), 2)
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(len(rejected_events), 1)
        self.assertEqual(confirmed[0].subject_id, proposal.id)
        self.assertEqual(rejected_events[0].subject_id, rejected.id)

        # 事件 payload 只放必要 id 与状态，不放金额明细与哈希。
        allowed = {
            "product_id",
            "price_proposal_id",
            "version_no",
            "state",
            "proposed_by",
            "party_id",
            "rejected_by",
            "actor_id",
        }
        for event in proposed + confirmed + rejected_events:
            self.assertLessEqual(set(event.payload), allowed)
            self.assertNotIn("suggested_fen", event.payload)
            self.assertNotIn("accepted_hash", event.payload)
        stored = await self.db.get(PriceProposal, rejected.id)
        await self.db.refresh(stored)
        self.assertEqual(stored.state, "withdrawn")

    # --- 6：事件重试不重复投递 -------------------------------------------------

    async def test_event_retry_does_not_duplicate_notification(self):
        with commerce_enabled():
            _agreement, version = await self._agreement_version("h-retry")
            await licensing.accept_agreement(
                self.db,
                user=self.owner,
                version_id=version.id,
                payload=AgreementAcceptanceIn(accepted_hash="h-retry"),
                request_id="beta16",
            )
        await self.db.commit()
        event = (await self._events(DomainEventType.AGREEMENT_ACCEPTED.value))[0]

        self.assertTrue(await notifications._process_event(self.db, event.id))
        await self.db.commit()
        before = await self.db.scalar(select(func.count()).select_from(Notification))
        # 第二次领取失败即视为重复消费，不再产生通知。
        self.assertFalse(await notifications._process_event(self.db, event.id))
        await self.db.commit()
        after = await self.db.scalar(select(func.count()).select_from(Notification))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
