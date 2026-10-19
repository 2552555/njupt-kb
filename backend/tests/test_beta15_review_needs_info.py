"""Beta15 审核「补证」与 beta15 事件通知（用户权责体系 §4.4、§14.1、§16.3；隔离 SQLite）。

覆盖：
- `decide(NEEDS_INFO)`：审核任务与版本审核状态变为 needs_info、**未发布**、发出
  `REVIEW_NEEDS_INFO` 事件、写入 ModerationDecision；理由缺失抛 InvalidInput；作者自审仍被拒；
- `REVIEW_NEEDS_INFO` 事件经事件消费后通知版本作者；
- `CONTRIBUTOR_APPLICATION_DECIDED` 事件经 `fanout_due_events` 生成 1 条通知，重复投递不重复，
  处理人即申请人时不通知自己；
- 未登记的事件类型不报错、不产生通知。

复用 `test_content_workflow.ContentWorkflowTests` 的内存 SQLite 脚手架。
"""

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import func, select, update

from app.core.enums import DomainEventType, ModerationDecisionType, ReviewStatus
from app.core.errors import Forbidden, InvalidInput
from app.core.events import emit_event
from app.core.ratelimit import reset_rate_limit
from app.modules.creator import service as creator
from app.modules.creator.schemas import ContributorApplicationCreate
from app.modules.identity.models import User
from app.modules.moderation import service as moderation
from app.modules.moderation.models import ModerationDecision, Review
from app.modules.notification import service as notifications
from app.modules.notification.models import DomainEvent, Notification

import test_content_workflow as workflow  # noqa: F401  注册 JSONB->JSON 编译


class Beta15ReviewNeedsInfoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.reviewer = User(
            email="reviewer@example.test",
            password_hash="unused",
            role="reviewer",
            status="active",
        )
        self.applicant = User(
            email="applicant@example.test", password_hash="unused", role="user", status="active"
        )
        self.db.add_all([self.admin, self.reviewer, self.applicant])
        await self.db.flush()
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _pending_review(self, submitter_id) -> Review:
        self.version.review_status = ReviewStatus.PENDING.value
        review = Review(
            resource_id=self.resource.id,
            version_id=self.version.id,
            submitter_id=submitter_id,
            status=ReviewStatus.PENDING.value,
            snapshot_revision=1,
            submitted_at=datetime.now(timezone.utc),
        )
        self.db.add(review)
        await self.db.flush()
        return review

    # --- 补证决定 -----------------------------------------------------------

    async def test_needs_info_sets_status_without_publishing(self):
        review = await self._pending_review(self.owner.id)
        record = await moderation.decide(
            self.db,
            review_id=review.id,
            reviewer=self.reviewer,
            decision=ModerationDecisionType.NEEDS_INFO,
            reason="请补充权利材料与作者关系证明",
            snapshot_revision=1,
            request_id="beta15",
        )
        await self.db.flush()

        self.assertEqual(record.decision, "needs_info")
        await self.db.refresh(review)
        self.assertEqual(review.status, "needs_info")
        await self.db.refresh(self.version)
        self.assertEqual(self.version.review_status, "needs_info")
        # 未发布：公开指针不切换、资源仍为未公开。
        await self.db.refresh(self.resource)
        self.assertIsNone(self.resource.published_version_id)
        self.assertEqual(self.resource.publication_status, "unpublished")

        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == DomainEventType.REVIEW_NEEDS_INFO.value
            )
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.subject_id, self.version.id)
        self.assertEqual(event.payload["decision"], "needs_info")
        stored = await self.db.scalar(
            select(ModerationDecision).where(ModerationDecision.review_id == review.id)
        )
        self.assertEqual(stored.decision, "needs_info")

    async def test_needs_info_requires_reason(self):
        review = await self._pending_review(self.owner.id)
        with self.assertRaises(InvalidInput):
            await moderation.decide(
                self.db,
                review_id=review.id,
                reviewer=self.reviewer,
                decision=ModerationDecisionType.NEEDS_INFO,
                reason="   ",
                snapshot_revision=1,
                request_id="beta15",
            )

    async def test_author_cannot_self_review_needs_info(self):
        review = await self._pending_review(self.reviewer.id)
        with self.assertRaises(Forbidden):
            await moderation.decide(
                self.db,
                review_id=review.id,
                reviewer=self.reviewer,
                decision=ModerationDecisionType.NEEDS_INFO,
                reason="自审尝试",
                snapshot_revision=1,
                request_id="beta15",
            )

    async def test_review_needs_info_event_notifies_author(self):
        review = await self._pending_review(self.owner.id)
        await moderation.decide(
            self.db,
            review_id=review.id,
            reviewer=self.reviewer,
            decision=ModerationDecisionType.NEEDS_INFO,
            reason="请补充权利材料",
            snapshot_revision=1,
            request_id="beta15",
        )
        await self.db.commit()
        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == DomainEventType.REVIEW_NEEDS_INFO.value
            )
        )
        self.assertTrue(await notifications._process_event(self.db, event.id))
        await self.db.commit()

        rows = list((await self.db.scalars(select(Notification))).all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, self.owner.id)
        self.assertEqual(rows[0].type, "review_needs_info")
        self.assertIn("补充", rows[0].body)

    # --- 事件通知 -----------------------------------------------------------

    async def test_unknown_event_type_is_processed_without_notification(self):
        event_id = await emit_event(
            self.db,
            event_type=DomainEventType.ORDER_PAID,
            subject_type="order",
            subject_id=uuid.uuid4(),
            payload={},
        )
        await self.db.commit()
        self.assertTrue(await notifications._process_event(self.db, event_id))
        self.assertEqual(await self.db.scalar(select(func.count(Notification.id))), 0)
        stored = await self.db.get(DomainEvent, event_id)
        self.assertEqual(stored.status, "processed")

    async def test_contributor_application_decided_fanout_is_delivered_once(self):
        await reset_rate_limit(f"contributor-application:{self.applicant.id}")
        application = await creator.submit_application(
            self.db,
            user=self.applicant,
            payload=ContributorApplicationCreate(
                capability_summary="长期维护课程资料", rights_materials_ref="ref"
            ),
            request_id="beta15",
        )
        await self.db.flush()
        await creator.decide_application(
            self.db,
            application_id=application.id,
            actor=self.reviewer,
            decision="under_review",
            reason="受理",
            request_id="beta15",
        )
        await self.db.flush()
        await creator.decide_application(
            self.db,
            application_id=application.id,
            actor=self.reviewer,
            decision="active",
            reason="通过",
            request_id="beta15",
        )
        await self.db.commit()

        # 只保留最终 active 结果事件，其余待处理事件直接置为已处理（不生成通知）。
        decided = list(
            (
                await self.db.scalars(
                    select(DomainEvent).where(
                        DomainEvent.event_type
                        == DomainEventType.CONTRIBUTOR_APPLICATION_DECIDED.value
                    )
                )
            ).all()
        )
        self.assertEqual(len(decided), 2)  # under_review + active
        active_event = next(e for e in decided if e.payload["status"] == "active")
        others = list(
            (
                await self.db.scalars(
                    select(DomainEvent.id).where(
                        DomainEvent.status == "pending", DomainEvent.id != active_event.id
                    )
                )
            ).all()
        )
        self.assertEqual(len(others), 2)  # 提交回执 + 受理决定
        await self.db.execute(
            update(DomainEvent)
            .where(DomainEvent.id.in_(others))
            .values(status="processed")
        )
        await self.db.commit()

        with patch.object(notifications, "async_session_factory", self.factory):
            processed = await notifications.fanout_due_events(self.db)
        self.assertEqual(processed, 1)

        rows = list(
            (
                await self.db.scalars(
                    select(Notification).where(Notification.user_id == self.applicant.id)
                )
            ).all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].type, "contributor_application_result")
        self.assertIn("已通过", rows[0].body)
        self.assertEqual(rows[0].data["application_id"], str(application.id))

        # 重复投递不产生第 2 条。
        with patch.object(notifications, "async_session_factory", self.factory):
            await notifications.fanout_due_events(self.db)
        self.assertEqual(
            await self.db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == self.applicant.id
                )
            ),
            1,
        )

    async def test_self_decided_application_does_not_notify_self(self):
        # reviewer 自己申请并自己审批：处理人即申请人，不给自己发通知。
        await reset_rate_limit(f"contributor-application:{self.reviewer.id}")
        application = await creator.submit_application(
            self.db,
            user=self.reviewer,
            payload=ContributorApplicationCreate(
                capability_summary="自我申请", rights_materials_ref="ref"
            ),
            request_id="beta15",
        )
        await self.db.flush()
        await creator.decide_application(
            self.db,
            application_id=application.id,
            actor=self.reviewer,
            decision="under_review",
            reason="受理",
            request_id="beta15",
        )
        await self.db.flush()
        await creator.decide_application(
            self.db,
            application_id=application.id,
            actor=self.reviewer,
            decision="active",
            reason="通过",
            request_id="beta15",
        )
        await self.db.commit()

        # 提交回执是申请人本人的动作，直接置为已处理（不生成通知）；
        # 审核结果事件的处理人即申请人，经 fanout 也应被跳过。
        await self.db.execute(
            update(DomainEvent)
            .where(
                DomainEvent.event_type
                == DomainEventType.CONTRIBUTOR_APPLICATION_SUBMITTED.value
            )
            .values(status="processed")
        )
        await self.db.commit()

        with patch.object(notifications, "async_session_factory", self.factory):
            await notifications.fanout_due_events(self.db)
        self.assertEqual(
            await self.db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == self.reviewer.id
                )
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
