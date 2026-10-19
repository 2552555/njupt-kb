"""Beta13 治理队列防滥用、运营告警、预览撤权、保留期清理（隔离 SQLite；不宣称生产并发结论）。

覆盖 buchong.md 仍未完成项：举报/申诉提交频控与未处理上限、运营告警只读聚合、
预览凭证撤销硬化、注销账号 30 天保留期自动清理。本文件只验证本地可执行的业务逻辑，
生产联调（PostgreSQL/Redis/MinIO）不在本机范围内。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from app.config import settings
from app.core.errors import Conflict, Forbidden, InvalidInput, RateLimited
from app.core.ratelimit import reset_rate_limit
from app.modules.access import service as access_service
from app.modules.access.models import PreviewGrant
from app.modules.content.models import Resource
from app.modules.identity import service as identity_service
from app.modules.identity.models import User
from app.modules.interaction import service as interaction_service
from app.modules.interaction.comment_management import manage_comment
from app.modules.moderation import comment_reports
from app.modules.moderation.models import AppealStatus, Report
from app.modules.ops import service as ops_service

import test_content_workflow as workflow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Beta13GovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 举报/申诉要求资源已发布且公开可见，与 beta10 契约一致。
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        from app.modules.access.models import AccessPolicy

        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.db.add(self.admin)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _make_comment(self, *, user=None, body="讨论内容"):
        return await interaction_service.create_comment(
            db=self.db,
            user_id=(user or self.owner).id,
            resource_id=self.resource.id,
            body=body,
            parent_id=None,
        )

    async def _hide(self, comment):
        return await manage_comment(
            self.db,
            user=self.admin,
            resource_id=self.resource.id,
            comment_id=comment.id,
            expected_revision=comment.revision,
            hidden=True,
            reason="不适宜内容",
            request_id="beta13",
            enforce_permission=False,
        )

    # --- 举报频控 -------------------------------------------------------------
    async def test_report_rate_limit_blocks_after_cap(self):
        await reset_rate_limit(f"comment-report:{self.other.id}")
        with patch.object(settings, "COMMENT_REPORT_RATE_LIMIT", 2), patch.object(
            settings, "COMMENT_REPORT_OPEN_CAP", 100
        ):
            for _ in range(2):
                comment = await self._make_comment()
                await comment_reports.create_comment_report(
                    self.db,
                    reporter=self.other,
                    resource_id=self.resource.id,
                    comment_id=comment.id,
                    reason="内容不当",
                    request_id="beta13",
                )
            third = await self._make_comment()
            with self.assertRaises(RateLimited):
                await comment_reports.create_comment_report(
                    self.db,
                    reporter=self.other,
                    resource_id=self.resource.id,
                    comment_id=third.id,
                    reason="内容不当",
                    request_id="beta13",
                )

    # --- 举报未处理上限 -------------------------------------------------------
    async def test_report_open_cap_blocks_after_limit(self):
        with patch.object(settings, "COMMENT_REPORT_OPEN_CAP", 2), patch.object(
            settings, "COMMENT_REPORT_RATE_LIMIT", 100
        ):
            for _ in range(2):
                comment = await self._make_comment()
                await comment_reports.create_comment_report(
                    self.db,
                    reporter=self.other,
                    resource_id=self.resource.id,
                    comment_id=comment.id,
                    reason="内容不当",
                    request_id="beta13",
                )
            third = await self._make_comment()
            with self.assertRaises(RateLimited):
                await comment_reports.create_comment_report(
                    self.db,
                    reporter=self.other,
                    resource_id=self.resource.id,
                    comment_id=third.id,
                    reason="内容不当",
                    request_id="beta13",
                )

    # --- 申诉频控 -------------------------------------------------------------
    async def test_appeal_rate_limit_blocks_after_cap(self):
        await reset_rate_limit(f"comment-appeal:{self.owner.id}")
        with patch.object(settings, "COMMENT_APPEAL_RATE_LIMIT", 2), patch.object(
            settings, "COMMENT_APPEAL_OPEN_CAP", 100
        ):
            for _ in range(2):
                comment = await self._make_comment(user=self.owner)
                await self._hide(comment)
                await comment_reports.create_comment_appeal(
                    self.db,
                    appellant=self.owner,
                    resource_id=self.resource.id,
                    comment_id=comment.id,
                    reason="误判",
                    request_id="beta13",
                )
            third = await self._make_comment(user=self.owner)
            await self._hide(third)
            with self.assertRaises(RateLimited):
                await comment_reports.create_comment_appeal(
                    self.db,
                    appellant=self.owner,
                    resource_id=self.resource.id,
                    comment_id=third.id,
                    reason="误判",
                    request_id="beta13",
                )

    # --- 申诉未处理上限 -------------------------------------------------------
    async def test_appeal_open_cap_blocks_after_limit(self):
        with patch.object(settings, "COMMENT_APPEAL_OPEN_CAP", 2), patch.object(
            settings, "COMMENT_APPEAL_RATE_LIMIT", 100
        ):
            for _ in range(2):
                comment = await self._make_comment(user=self.owner)
                await self._hide(comment)
                await comment_reports.create_comment_appeal(
                    self.db,
                    appellant=self.owner,
                    resource_id=self.resource.id,
                    comment_id=comment.id,
                    reason="误判",
                    request_id="beta13",
                )
            third = await self._make_comment(user=self.owner)
            await self._hide(third)
            with self.assertRaises(RateLimited):
                await comment_reports.create_comment_appeal(
                    self.db,
                    appellant=self.owner,
                    resource_id=self.resource.id,
                    comment_id=third.id,
                    reason="误判",
                    request_id="beta13",
                )


class Beta13OpsAlertsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        from app.modules.access.models import AccessPolicy

        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.db.add(self.admin)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _open_report(self):
        from app.modules.interaction import service as interaction_service

        comment = await interaction_service.create_comment(
            db=self.db,
            user_id=self.owner.id,
            resource_id=self.resource.id,
            body="待举报",
            parent_id=None,
        )
        return await comment_reports.create_comment_report(
            self.db,
            reporter=self.other,
            resource_id=self.resource.id,
            comment_id=comment.id,
            reason="内容不当",
            request_id="beta13",
        )

    async def test_ops_alert_fires_when_threshold_reached(self):
        with patch.object(settings, "OPS_ALERT_OPEN_REPORTS", 1), patch.object(
            settings, "OPS_ALERT_CRITICAL_MULTIPLIER", 1
        ):
            await self._open_report()
            result = await ops_service.check_ops_alerts(self.db)
            keys = {a.key for a in result.alerts}
            self.assertIn("open_reports", keys)
            alert = next(a for a in result.alerts if a.key == "open_reports")
            # 阈值 1、critical 倍率 1，单一未处理举报即达 critical。
            self.assertEqual(alert.level, "critical")
            self.assertTrue(result.has_critical)

    async def test_ops_alert_absent_when_below_threshold(self):
        with patch.object(settings, "OPS_ALERT_OPEN_REPORTS", 50):
            result = await ops_service.check_ops_alerts(self.db)
            self.assertFalse(any(a.key == "open_reports" for a in result.alerts))
            self.assertFalse(result.has_critical)


class Beta13PreviewRevokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.db.add(self.admin)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    def _grant(self, *, status="issued", user=None):
        return PreviewGrant(
            resource_id=self.resource.id,
            version_id=self.version.id,
            user_id=(user or self.other).id,
            file_id=None,
            derivative_id=None,
            status=status,
            valid_until=_now() + timedelta(hours=1),
        )

    async def test_revoke_single_preview_grant(self):
        grant = self._grant()
        self.db.add(grant)
        await self.db.flush()
        out = await access_service.revoke_preview_grant(
            self.db,
            actor=self.admin,
            grant_id=grant.id,
            reason="版权下架",
            request_id="beta13",
        )
        self.assertEqual(out.status, "revoked")
        stored = await self.db.get(PreviewGrant, grant.id)
        self.assertEqual(stored.status, "revoked")

    async def test_revoke_preview_grant_idempotent(self):
        grant = self._grant()
        self.db.add(grant)
        await self.db.flush()
        await access_service.revoke_preview_grant(
            self.db, actor=self.admin, grant_id=grant.id, reason="版权下架", request_id="beta13"
        )
        with self.assertRaises(Conflict):
            await access_service.revoke_preview_grant(
                self.db, actor=self.admin, grant_id=grant.id, reason="再次", request_id="beta13"
            )

    async def test_revoke_resource_previews_only_active(self):
        active1 = self._grant()
        active2 = self._grant()
        already = self._grant(status="revoked")
        self.db.add_all([active1, active2, already])
        await self.db.flush()
        count = await access_service.revoke_resource_previews(
            self.db,
            actor=self.admin,
            resource_id=self.resource.id,
            reason="整资源下架",
            request_id="beta13",
        )
        self.assertEqual(count, 2)
        for gid in (active1.id, active2.id):
            self.assertEqual((await self.db.get(PreviewGrant, gid)).status, "revoked")
        # 已撤销的不重复计数。
        self.assertEqual((await self.db.get(PreviewGrant, already.id)).status, "revoked")


class Beta13RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.course = self.course  # 复用基类课程

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def test_retention_purges_expired_deleted_accounts(self):
        old = User(
            email="old@example.test",
            password_hash="unused",
            role="user",
            status="deleted",
            deleted_at=_now() - timedelta(days=40),
        )
        self.db.add(old)
        await self.db.flush()
        # 业务记录：资源归属该账号，清理后不得被删除或改主。
        res = Resource(
            course_id=self.course.id,
            owner_id=old.id,
            resource_type="note",
            publication_status="published",
        )
        self.db.add(res)
        await self.db.flush()

        count = await identity_service.run_retention_cleanup(self.db, retention_days=30)
        self.assertEqual(count, 1)
        await self.db.refresh(old)
        self.assertIsNotNone(old.purged_at)
        self.assertTrue(old.email.startswith("purged+"))
        self.assertIsNone(old.email_verified_at)
        # 用户行与业务记录保留。
        self.assertIsNotNone(await self.db.get(User, old.id))
        stored = await self.db.get(Resource, res.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.owner_id, old.id)

    async def test_retention_skips_accounts_within_window(self):
        recent = User(
            email="recent@example.test",
            password_hash="unused",
            role="user",
            status="deleted",
            deleted_at=_now() - timedelta(days=1),
        )
        self.db.add(recent)
        await self.db.flush()
        count = await identity_service.run_retention_cleanup(self.db, retention_days=30)
        self.assertEqual(count, 0)
        await self.db.refresh(recent)
        self.assertIsNone(recent.purged_at)
        self.assertNotIn("purged+", recent.email)


if __name__ == "__main__":
    unittest.main()
