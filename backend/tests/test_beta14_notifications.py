"""Beta14 通知补全：纠错处理事件转为站内通知（隔离 SQLite；不宣称生产并发结论）。

覆盖 notification/service.py 的 `_EVENT_HANDLERS` 对纠错事件的登记：
`CORRECTION_RESOLVED` 通知纠错提交人（带资源/版本跳转数据），处理人即提交人时
不误发；`CORRECTION_CREATED` 是提交人本人动作，不产生通知。测试经
`_process_event` 走真实事件消费链路，验证业务处理只保留一个通知入口。
"""

import unittest
import uuid

from sqlalchemy import func, select

from app.core.enums import CorrectionStatus, DomainEventType
from app.modules.identity.models import User
from app.modules.interaction.models import Correction
from app.modules.notification import service as notifications
from app.modules.notification.models import DomainEvent, Notification

import test_content_workflow as workflow


class Beta14CorrectionNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.db.add(self.admin)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def test_status_update_and_event_retry_deliver_only_once(self):
        from app.modules.interaction.service import update_correction_status

        correction = await self._correction()
        await update_correction_status(
            self.db, actor_id=self.admin.id, correction_id=correction.id,
            status=CorrectionStatus.REJECTED, resolution_note="已核对",
            fixed_version_id=None, request_id="beta14-correction",
        )
        await self.db.commit()
        event = (await self.db.execute(select(DomainEvent).where(
            DomainEvent.subject_id == correction.id,
            DomainEvent.event_type == DomainEventType.CORRECTION_RESOLVED.value,
        ))).scalar_one()
        self.assertEqual(await self.db.scalar(select(func.count()).select_from(Notification)), 0)
        await notifications._process_event(self.db, event.id)
        await self.db.commit()
        await notifications._process_event(self.db, event.id)
        await self.db.commit()
        self.assertEqual(await self.db.scalar(select(func.count()).select_from(Notification)), 1)

    async def _correction(self, *, submitter=None, handled_by=None):
        correction = Correction(
            resource_id=self.resource.id,
            version_id=self.version.id,
            submitter_id=(submitter or self.other).id,
            description="资料类型应为试卷",
            status="open",
            handled_by=(handled_by or self.admin).id,
        )
        self.db.add(correction)
        await self.db.flush()
        return correction

    async def _emit(self, event_type, *, subject_id, payload, dedupe_key):
        from app.core.events import emit_event

        event_id = await emit_event(
            self.db,
            event_type=event_type,
            subject_type="correction",
            subject_id=subject_id,
            payload=payload,
            dedupe_key=dedupe_key,
        )
        await self.db.commit()
        return event_id

    async def test_resolved_event_notifies_submitter(self):
        correction = await self._correction()
        event_id = await self._emit(
            DomainEventType.CORRECTION_RESOLVED,
            subject_id=correction.id,
            payload={"status": "accepted", "submitter_id": str(self.other.id)},
            dedupe_key=f"correction.resolved:{correction.id}:accepted",
        )
        self.assertTrue(await notifications._process_event(self.db, event_id))
        rows = list((await self.db.scalars(select(Notification))).all())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # 收件人是纠错提交人，与资料所有权解耦。
        self.assertEqual(row.user_id, self.other.id)
        self.assertEqual(row.type, "correction_result")
        self.assertIn("纠错", row.title)
        self.assertIn("已采纳", row.body)
        # 跳转数据指向纠错所在的资源与版本。
        self.assertEqual(row.data["correction_id"], str(correction.id))
        self.assertEqual(row.data["resource_id"], str(self.resource.id))
        self.assertEqual(row.data["version_id"], str(self.version.id))
        self.assertEqual(row.data["status"], "accepted")
        # 重复消费同一事件不再产生通知。
        self.assertFalse(await notifications._process_event(self.db, event_id))
        self.assertEqual(await self.db.scalar(select(func.count(Notification.id))), 1)

    async def test_self_handled_correction_is_not_notified(self):
        # 提交人自己处理自己的工单：不给自己发通知。
        correction = await self._correction(submitter=self.other, handled_by=self.other)
        event_id = await self._emit(
            DomainEventType.CORRECTION_RESOLVED,
            subject_id=correction.id,
            payload={"status": "fixed", "submitter_id": str(self.other.id)},
            dedupe_key=f"correction.resolved:{correction.id}:fixed",
        )
        self.assertTrue(await notifications._process_event(self.db, event_id))
        self.assertEqual(await self.db.scalar(select(func.count(Notification.id))), 0)
        stored = await self.db.get(DomainEvent, event_id)
        self.assertEqual(stored.status, "processed")

    async def test_created_event_produces_no_notification(self):
        # CORRECTION_CREATED 是提交人本人动作，无需告诉自己。
        correction = await self._correction()
        event_id = await self._emit(
            DomainEventType.CORRECTION_CREATED,
            subject_id=correction.id,
            payload={
                "resource_id": str(self.resource.id),
                "version_id": str(self.version.id),
                "submitter_id": str(self.other.id),
            },
            dedupe_key=f"correction.created:{correction.id}",
        )
        self.assertTrue(await notifications._process_event(self.db, event_id))
        self.assertEqual(await self.db.scalar(select(func.count(Notification.id))), 0)

    async def test_resolved_event_without_correction_row_is_dropped(self):
        # 工单行已不存在（如级联删除）时不凭空发放通知。
        event_id = await self._emit(
            DomainEventType.CORRECTION_RESOLVED,
            subject_id=uuid.uuid4(),
            payload={"status": "accepted", "submitter_id": str(self.other.id)},
            dedupe_key="correction.resolved:missing-row",
        )
        self.assertTrue(await notifications._process_event(self.db, event_id))
        self.assertEqual(await self.db.scalar(select(func.count(Notification.id))), 0)

    async def test_resolved_event_falls_back_to_correction_submitter(self):
        # payload 缺失 submitter_id 时回落到工单行的提交人。
        correction = await self._correction()
        event_id = await self._emit(
            DomainEventType.CORRECTION_RESOLVED,
            subject_id=correction.id,
            payload={"status": "rejected"},
            dedupe_key=f"correction.resolved:{correction.id}:rejected",
        )
        self.assertTrue(await notifications._process_event(self.db, event_id))
        rows = list((await self.db.scalars(select(Notification))).all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, self.other.id)
        self.assertIn("未采纳", rows[0].body)


if __name__ == "__main__":
    unittest.main()
