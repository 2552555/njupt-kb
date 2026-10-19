"""本人举报/申诉工单：身份隔离、撤权遮蔽、游标和状态轨迹（隔离 SQLite）。"""

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.core.enums import ReportStatus
from app.core.errors import InvalidInput, NotFound
from app.modules.access.models import AccessPolicy
from app.modules.content.models import ResourceVersion
from app.modules.interaction.models import Comment
from app.modules.moderation import comment_reports, personal
from app.modules.moderation.models import (
    AppealStatus,
    CommentAppeal,
    Report,
    ReportEvent,
)

import test_content_workflow as workflow


class PersonalReportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = workflow.ContentWorkflowTests.asyncSetUp
    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown
    publish_public = workflow.ContentWorkflowTests.publish_public

    async def _report(self, *, reporter=None, status="open", comment_id=None):
        row = Report(
            resource_id=self.resource.id,
            version_id=self.version.id,
            reporter_id=(reporter or self.other).id,
            reason_code="comment" if comment_id else "copyright",
            description="本人提供的说明",
            status=status,
            comment_id=comment_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def test_only_own_reports_even_for_owner_or_admin(self):
        mine = await self._report()
        await self._report(reporter=self.owner)
        self.other.role = "admin"
        result = await personal.list_reports(self.db, user=self.other)
        self.assertEqual([item.id for item in result.items], [mine.id])

    async def test_detail_and_events_reject_other_reporter(self):
        row = await self._report()
        for report_id in (row.id, uuid.uuid4()):
            with self.assertRaises(NotFound):
                await personal.get_report(self.db, user=self.owner, report_id=report_id)
            with self.assertRaises(NotFound):
                await personal.list_report_events(
                    self.db, user=self.owner, report_id=report_id
                )

    async def test_revoked_metadata_masks_targets_but_keeps_own_reason(self):
        await self.publish_public()
        row = await self._report()
        before = await personal.get_report(self.db, user=self.other, report_id=row.id)
        self.assertTrue(before.available)
        self.assertEqual(before.title, "public note")
        policy = await self.db.scalar(select(AccessPolicy))
        policy.metadata_scope = "private"
        await self.db.flush()
        after = await personal.get_report(self.db, user=self.other, report_id=row.id)
        self.assertFalse(after.available)
        self.assertIsNone(after.resource_id)
        self.assertIsNone(after.version_id)
        self.assertIsNone(after.title)
        self.assertEqual(after.description, "本人提供的说明")

    async def test_unpublished_and_draft_version_never_exposed(self):
        row = await self._report()
        result = await personal.get_report(self.db, user=self.other, report_id=row.id)
        self.assertFalse(result.available)
        self.assertIsNone(result.title)
        await self.publish_public()
        draft = ResourceVersion(
            resource_id=self.resource.id,
            created_by=self.owner.id,
            version_no=2,
            title="未公开的草稿标题",
            review_status="draft",
        )
        self.db.add(draft)
        await self.db.flush()
        row.version_id = draft.id
        result = await personal.get_report(self.db, user=self.other, report_id=row.id)
        self.assertTrue(result.available)
        self.assertIsNone(result.version_id)

    async def test_status_filter_and_cursor_no_duplicates(self):
        for _ in range(3):
            await self._report(status="resolved")
        await self._report(status="open")
        first = await personal.list_reports(
            self.db,
            user=self.other,
            statuses=[ReportStatus.RESOLVED],
            limit=2,
        )
        self.assertTrue(first.has_more)
        second = await personal.list_reports(
            self.db,
            user=self.other,
            statuses=[ReportStatus.RESOLVED],
            limit=2,
            cursor=first.next_cursor,
        )
        self.assertFalse(second.has_more)
        self.assertEqual(len({item.id for item in first.items + second.items}), 3)
        with self.assertRaises(InvalidInput):
            await personal.list_reports(self.db, user=self.other, cursor="bad")

    async def test_progress_does_not_expose_internal_evidence_or_note(self):
        row = await self._report()
        for state in ("open", "triaged", "investigating"):
            self.db.add(
                ReportEvent(
                    report_id=row.id,
                    actor_id=self.owner.id,
                    from_status=None,
                    to_status=state,
                    note="内部调查备注",
                    evidence={"private": "credential"},
                )
            )
        await self.db.flush()
        page = await personal.list_report_events(
            self.db, user=self.other, report_id=row.id, limit=2
        )
        self.assertTrue(page.has_more)
        more = await personal.list_report_events(
            self.db,
            user=self.other,
            report_id=row.id,
            cursor=page.next_cursor,
            limit=2,
        )
        self.assertEqual(len({item.id for item in page.items + more.items}), 3)
        for item in page.items + more.items:
            self.assertEqual(
                set(item.model_dump()), {"id", "from_status", "to_status", "created_at"}
            )

    async def test_comment_report_creation_and_decision_append_status_events(self):
        await self.publish_public()
        comment = Comment(
            resource_id=self.resource.id, user_id=self.owner.id, body="正文"
        )
        self.db.add(comment)
        await self.db.flush()
        with patch.object(
            comment_reports, "check_rate_limit", AsyncMock(return_value=True)
        ):
            out = await comment_reports.create_comment_report(
                self.db,
                reporter=self.other,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="举报原因",
                request_id="test",
            )
        await comment_reports.decide_comment_report(
            self.db,
            report_id=out.id,
            actor=self.owner,
            action="dismiss",
            reason="内部理由",
            expected_revision=1,
            comment_revision=None,
            request_id="test",
        )
        page = await personal.list_report_events(
            self.db, user=self.other, report_id=out.id
        )
        self.assertEqual(
            {event.to_status for event in page.items}, {"open", "dismissed"}
        )
        self.assertEqual(
            (await personal.list_reports(self.db, user=self.other, kind="comment"))
            .items[0]
            .id,
            out.id,
        )
        self.assertEqual(
            (
                await personal.list_reports(self.db, user=self.other, kind="resource")
            ).items,
            [],
        )

    async def test_appeals_filter_ownership_and_redact_inaccessible_target(self):
        comment = Comment(
            resource_id=self.resource.id,
            user_id=self.other.id,
            body="隐藏",
            is_hidden=True,
        )
        other_comment = Comment(
            resource_id=self.resource.id, user_id=self.owner.id, body="其他"
        )
        self.db.add_all([comment, other_comment])
        await self.db.flush()
        mine = CommentAppeal(
            resource_id=self.resource.id,
            comment_id=comment.id,
            appellant_id=self.other.id,
            reason="本人申诉理由",
            status="rejected",
            handled_by=self.owner.id,
        )
        self.db.add_all(
            [
                mine,
                CommentAppeal(
                    resource_id=self.resource.id,
                    comment_id=other_comment.id,
                    appellant_id=self.owner.id,
                    reason="其他人的理由",
                    status="pending",
                ),
            ]
        )
        await self.db.flush()
        result = await personal.list_comment_appeals(
            self.db, user=self.other, statuses=[AppealStatus.REJECTED]
        )
        self.assertEqual([item.id for item in result.items], [mine.id])
        item = result.items[0]
        self.assertIsNone(item.resource_id)
        self.assertIsNone(item.comment_id)
        self.assertFalse(item.available)
        self.assertEqual(item.reason, "本人申诉理由")
        self.assertNotIn("handled_by", item.model_dump())
        self.assertEqual(
            (
                await personal.list_comment_appeals(
                    self.db,
                    user=self.other,
                    statuses=[AppealStatus.PENDING],
                )
            ).items,
            [],
        )
