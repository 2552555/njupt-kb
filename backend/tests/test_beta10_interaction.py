"""Beta10 推荐探索槽位与评论举报/申诉契约（隔离 SQLite；不宣称生产并发结论）。"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.errors import Conflict, Forbidden, InvalidInput
from app.modules.access.models import AccessPolicy
from app.modules.content.models import Resource, ResourceVersion
from app.modules.identity.models import User
from app.modules.interaction import recommendations, service
from app.modules.interaction.comment_management import manage_comment
from app.modules.interaction.models import Comment, Rating
from app.modules.moderation import comment_reports
from app.modules.moderation.models import AppealStatus, Report
import test_content_workflow as workflow


class Beta10InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.db.add(self.admin)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #

    async def publish_resource(
        self, *, title, created_at, updated_at, ratings=0, freshness="current"
    ):
        resource = Resource(
            course_id=self.course.id,
            owner_id=self.owner.id,
            resource_type="note",
            publication_status="published",
            freshness_status=freshness,
            created_at=created_at,
        )
        self.db.add(resource)
        await self.db.flush()
        version = ResourceVersion(
            resource_id=resource.id,
            created_by=self.owner.id,
            version_no=1,
            title=title,
            review_status="approved",
        )
        self.db.add(version)
        await self.db.flush()
        # 显式设置 updated_at，覆盖 Timestamped.onupdate 的当前时间，使排序可控。
        resource.published_version_id = version.id
        resource.updated_at = updated_at
        raters = [self.owner, self.other, self.admin]
        for index in range(ratings):
            self.db.add(
                Rating(
                    user_id=raters[index].id,
                    resource_id=resource.id,
                    version_id=version.id,
                    score=5,
                )
            )
        await self.db.flush()
        return resource, version

    async def make_comment(self, *, body="讨论内容", user=None):
        item = await service.create_comment(
            db=self.db,
            user_id=(user or self.owner).id,
            resource_id=self.resource.id,
            body=body,
            parent_id=None,
        )
        return item

    # ------------------------------------------------------------------ #
    # 探索槽位
    # ------------------------------------------------------------------ #

    async def test_explore_slot_appears_at_fixed_position_with_reason(self):
        now = datetime.now(timezone.utc)
        await self.publish_resource(
            title="规则一", created_at=now, updated_at=now - timedelta(hours=1)
        )
        await self.publish_resource(
            title="规则二", created_at=now, updated_at=now - timedelta(hours=2)
        )
        await self.publish_resource(
            title="规则三", created_at=now, updated_at=now - timedelta(hours=3)
        )
        candidate, _ = await self.publish_resource(
            title="新内容候选",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=5),
        )

        result = await recommendations.recommend_resources(
            self.db, user=None, course_id=None, limit=4
        )
        self.assertEqual(len(result.items), 4)
        explores = [item for item in result.items if item.explore]
        self.assertEqual(len(explores), 1)
        self.assertEqual(explores[0].resource_id, candidate.id)
        self.assertIn(recommendations.EXPLORE_REASON, explores[0].reasons)
        # 插入在固定位置（第 3 条，0-based 索引 2）。
        self.assertTrue(result.items[2].explore)
        # 不重复：探索项 ID 不出现在其余项中。
        ids = [item.resource_id for item in result.items]
        self.assertEqual(len(ids), len(set(ids)))

    async def test_explore_slot_not_enabled_below_min_limit(self):
        now = datetime.now(timezone.utc)
        await self.publish_resource(
            title="规则一", created_at=now, updated_at=now - timedelta(hours=1)
        )
        await self.publish_resource(
            title="规则二", created_at=now, updated_at=now - timedelta(hours=2)
        )
        await self.publish_resource(
            title="规则三", created_at=now, updated_at=now - timedelta(hours=3)
        )
        result = await recommendations.recommend_resources(
            self.db, user=None, course_id=None, limit=3
        )
        self.assertTrue(all(not item.explore for item in result.items))

    async def test_explore_slot_absent_without_qualified_candidate(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=40)
        for index in range(4):
            await self.publish_resource(
                title=f"旧内容{index}", created_at=old, updated_at=old
            )
        result = await recommendations.recommend_resources(
            self.db, user=None, course_id=None, limit=4
        )
        self.assertEqual(len(result.items), 4)
        self.assertTrue(all(not item.explore for item in result.items))

    async def test_explore_slot_skips_recent_but_well_rated_content(self):
        now = datetime.now(timezone.utc)
        await self.publish_resource(
            title="规则一", created_at=now, updated_at=now - timedelta(hours=1)
        )
        await self.publish_resource(
            title="规则二", created_at=now, updated_at=now - timedelta(hours=2)
        )
        await self.publish_resource(
            title="规则三", created_at=now, updated_at=now - timedelta(hours=3)
        )
        # 近期但已有 3 条评价：不满足「新内容」条件，不占用探索槽位。
        # 用 possibly_outdated 使其排在规则项之后，从而成为唯一未入选规则项的候选。
        await self.publish_resource(
            title="已被评价",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
            ratings=3,
            freshness="possibly_outdated",
        )
        result = await recommendations.recommend_resources(
            self.db, user=None, course_id=None, limit=4
        )
        self.assertTrue(all(not item.explore for item in result.items))

    # ------------------------------------------------------------------ #
    # 评论举报
    # ------------------------------------------------------------------ #

    async def test_report_requires_reason_and_rejects_self_report(self):
        comment = await self.make_comment()
        with self.assertRaises(InvalidInput):
            await comment_reports.create_comment_report(
                self.db,
                reporter=self.other,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="   ",
                request_id="beta10",
            )
        with self.assertRaises(Forbidden):
            await comment_reports.create_comment_report(
                self.db,
                reporter=self.owner,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="我是作者",
                request_id="beta10",
            )
        created = await comment_reports.create_comment_report(
            self.db,
            reporter=self.other,
            resource_id=self.resource.id,
            comment_id=comment.id,
            reason="内容不当",
            request_id="beta10",
        )
        self.assertEqual(created.comment_id, comment.id)
        self.assertEqual(created.reason, "内容不当")
        self.assertEqual(created.revision, 1)

    async def test_duplicate_active_report_is_rejected(self):
        comment = await self.make_comment()
        await comment_reports.create_comment_report(
            self.db,
            reporter=self.other,
            resource_id=self.resource.id,
            comment_id=comment.id,
            reason="第一次",
            request_id="beta10",
        )
        with self.assertRaises(Conflict):
            await comment_reports.create_comment_report(
                self.db,
                reporter=self.other,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="第二次",
                request_id="beta10",
            )

    async def test_decide_report_reuses_comment_hiding(self):
        comment = await self.make_comment()
        report = await comment_reports.create_comment_report(
            self.db,
            reporter=self.other,
            resource_id=self.resource.id,
            comment_id=comment.id,
            reason="不当内容",
            request_id="beta10",
        )
        resolved = await comment_reports.decide_comment_report(
            self.db,
            report_id=report.id,
            actor=self.admin,
            action="resolve",
            reason="确认违规，隐藏评论",
            expected_revision=1,
            comment_revision=comment.revision,
            request_id="beta10",
        )
        self.assertEqual(resolved.status.value, "resolved")
        self.assertEqual(resolved.revision, 2)
        stored = await self.db.get(Comment, comment.id)
        await self.db.refresh(stored)
        self.assertTrue(stored.is_hidden)
        self.assertEqual(stored.revision, 2)

    # ------------------------------------------------------------------ #
    # 评论申诉
    # ------------------------------------------------------------------ #

    async def test_hidden_comment_author_can_appeal_once_and_restore(self):
        comment = await self.make_comment()
        hidden = await manage_comment(
            self.db,
            user=self.admin,
            resource_id=self.resource.id,
            comment_id=comment.id,
            expected_revision=comment.revision,
            hidden=True,
            reason="不适宜内容",
            request_id="beta10",
        )
        appeal = await comment_reports.create_comment_appeal(
            self.db,
            appellant=self.owner,
            resource_id=self.resource.id,
            comment_id=comment.id,
            reason="请复核",
            request_id="beta10",
        )
        self.assertEqual(appeal.status, AppealStatus.PENDING)
        with self.assertRaises(Conflict):
            await comment_reports.create_comment_appeal(
                self.db,
                appellant=self.owner,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="再次申诉",
                request_id="beta10",
            )
        decided = await comment_reports.decide_comment_appeal(
            self.db,
            appeal_id=appeal.id,
            actor=self.admin,
            decision="accept",
            reason="申诉成立",
            expected_revision=1,
            comment_revision=hidden.revision,
            request_id="beta10",
        )
        self.assertEqual(decided.status, AppealStatus.ACCEPTED)
        stored = await self.db.get(Comment, comment.id)
        await self.db.refresh(stored)
        self.assertFalse(stored.is_hidden)

    async def test_appeal_requires_hidden_comment(self):
        comment = await self.make_comment()
        with self.assertRaises(Conflict):
            await comment_reports.create_comment_appeal(
                self.db,
                appellant=self.owner,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="未被隐藏也想申诉",
                request_id="beta10",
            )

    async def test_withdrawn_comment_cannot_be_appealed(self):
        comment = await self.make_comment()
        await manage_comment(
            self.db,
            user=self.admin,
            resource_id=self.resource.id,
            comment_id=comment.id,
            expected_revision=comment.revision,
            hidden=True,
            reason="先隐藏",
            request_id="beta10",
        )
        stored = await self.db.get(Comment, comment.id)
        await self.db.refresh(stored)
        stored.withdrawn_at = datetime.now(timezone.utc)
        await self.db.flush()
        with self.assertRaises(Conflict):
            await comment_reports.create_comment_appeal(
                self.db,
                appellant=self.owner,
                resource_id=self.resource.id,
                comment_id=comment.id,
                reason="已撤回仍申诉",
                request_id="beta10",
            )

    async def test_non_admin_cannot_handle_comment_report(self):
        import httpx

        from app.config import settings
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        comment = await self.make_comment()
        report = await comment_reports.create_comment_report(
            self.db,
            reporter=self.other,
            resource_id=self.resource.id,
            comment_id=comment.id,
            reason="需要处理",
            request_id="beta10",
        )

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                path = f"/api/v1/admin/comment-reports/{report.id}/decision"
                payload = {
                    "action": "accept",
                    "reason": "受理",
                    "expected_revision": 1,
                }
                app.dependency_overrides[get_current_user] = lambda: self.other
                response = await client.post(path, json=payload)
                self.assertEqual(response.status_code, 403, response.text)
                app.dependency_overrides[get_current_user] = lambda: self.admin
                response = await client.post(path, json=payload)
                self.assertEqual(response.status_code, 200, response.text)
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
