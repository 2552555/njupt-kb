"""beta15 只读 SLO 指标端点与事件对账/重放测试（隔离 SQLite；不宣称生产并发结论）。

覆盖：GET /admin/metrics 需要 METRICS_VIEW（无权 403）、开关关闭时 NotEnabled（503）、
计数与实际数据一致；reconcile_events 计数正确；replay_failed_events 把 failed 重置为
pending、幂等、跳过超限毒任务、重放后不产生重复通知。
"""

import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from app.config import settings
from app.core.errors import NotEnabled
from app.modules.identity.models import User
from app.modules.jobs.models import Job
from app.modules.moderation.models import AuditLog
from app.modules.notification import service as notification_service
from app.modules.notification.models import DomainEvent, Notification
from app.modules.notification.reconcile import reconcile_events, replay_failed_events
from app.modules.ops import service as ops_service

import test_content_workflow as workflow


class Beta15OpsMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="metrics-admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.db.add(self.admin)
        await self.db.flush()
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    def _event(self, *, status="pending", attempts=0, dedupe_key=None, event_type="request.created"):
        return DomainEvent(
            event_type=event_type,
            subject_type="resource_request",
            subject_id=self.resource.id,
            payload={},
            status=status,
            attempts=attempts,
            dedupe_key=dedupe_key,
        )

    def _client(self):
        import httpx
        from app.main import app

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:3000",
            headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
            cookies={settings.CSRF_COOKIE_NAME: "test"},
        )

    async def _override(self, user):
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: user

    # --- 权限与开关 -----------------------------------------------------------

    async def test_metrics_requires_metrics_view(self):
        from app.main import app

        try:
            await self._override(self.owner)  # contributor：无 metrics.view
            async with self._client() as client:
                self.assertEqual(
                    (await client.get("/api/v1/admin/metrics")).status_code, 403
                )
            await self._override(self.admin)
            async with self._client() as client:
                self.assertEqual(
                    (await client.get("/api/v1/admin/metrics")).status_code, 200
                )
        finally:
            app.dependency_overrides.clear()

    async def test_metrics_disabled_raises_not_enabled(self):
        with patch.object(settings, "OPS_METRICS_ENABLED", False):
            with self.assertRaises(NotEnabled):
                await ops_service.metrics_summary(self.db)

    async def test_metrics_endpoint_disabled_returns_503(self):
        from app.main import app

        try:
            await self._override(self.admin)
            async with self._client() as client:
                with patch.object(settings, "OPS_METRICS_ENABLED", False):
                    response = await client.get("/api/v1/admin/metrics")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "FEATURE_NOT_ENABLED")
        finally:
            app.dependency_overrides.clear()

    # --- 计数与数据库一致 -----------------------------------------------------

    async def test_metrics_counts_match_database(self):
        from app.main import app

        self.db.add_all(
            [
                self._event(status="pending"),
                self._event(status="processing", attempts=1),
                self._event(status="processed", attempts=1),
                self._event(status="failed", attempts=1),
                self._event(
                    status="failed", attempts=settings.EVENT_REPLAY_MAX_ATTEMPTS
                ),
                Job(
                    job_type="file.scan",
                    target_id="job-a",
                    processor_version="scan/2",
                    status="queued",
                ),
                Job(
                    job_type="file.scan",
                    target_id="job-b",
                    processor_version="scan/2",
                    status="failed",
                ),
            ]
        )
        await self.db.commit()

        try:
            await self._override(self.admin)
            async with self._client() as client:
                response = await client.get("/api/v1/admin/metrics")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()

            self.assertEqual(
                body["events"],
                {
                    "pending": 1,
                    "processing": 1,
                    "processed": 1,
                    "failed": 2,
                    "over_max_attempts": 1,
                    "duplicate_dedupe_keys": 0,
                },
            )
            self.assertEqual(body["jobs"]["queued"], 1)
            self.assertEqual(body["jobs"]["failed"], 1)
            self.assertEqual(body["jobs"]["total"], 2)
            self.assertEqual(body["db_pool"]["pool_size"], settings.DB_POOL_SIZE)
            self.assertEqual(body["db_pool"]["max_overflow"], settings.DB_MAX_OVERFLOW)
            self.assertEqual(
                body["db_pool"]["pool_timeout_seconds"], settings.DB_POOL_TIMEOUT_SECONDS
            )
            self.assertEqual(body["slow_query_ms"], settings.SLOW_QUERY_MS)
            self.assertIn("entries", body["cache"])
            self.assertIn("by_class", body["cache"])
        finally:
            app.dependency_overrides.clear()

    # --- 对账 ---------------------------------------------------------------

    async def test_reconcile_events_counts(self):
        self.db.add_all(
            [
                self._event(status="pending"),
                self._event(status="failed", attempts=1, dedupe_key="dup"),
                self._event(
                    status="failed",
                    attempts=settings.EVENT_REPLAY_MAX_ATTEMPTS,
                    dedupe_key="dup",
                    event_type="request.fulfilled",  # 跨 event_type 才会重复 dedupe_key
                ),
            ]
        )
        await self.db.commit()

        result = await reconcile_events(self.db)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["processing"], 0)
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(result["over_max_attempts"], 1)
        self.assertEqual(result["duplicate_dedupe_keys"], 1)

    # --- 重放 ---------------------------------------------------------------

    async def test_replay_failed_events_resets_and_is_idempotent(self):
        self.db.add_all(
            [
                self._event(status="failed", attempts=1),
                self._event(status="failed", attempts=2),
            ]
        )
        await self.db.commit()

        first = await replay_failed_events(self.db, limit=100)
        self.assertEqual(first, 2)
        await self.db.commit()

        statuses = (await self.db.execute(select(DomainEvent.status))).scalars().all()
        self.assertEqual(set(statuses), {"pending"})

        # 幂等：再次调用没有可重放项，不再重置、不重复写审计。
        second = await replay_failed_events(self.db, limit=100)
        self.assertEqual(second, 0)
        await self.db.commit()

        audit_count = await self.db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "event.replay")
        )
        self.assertEqual(audit_count, 1)

    async def test_replay_skips_over_max_attempts(self):
        self.db.add(
            self._event(status="failed", attempts=settings.EVENT_REPLAY_MAX_ATTEMPTS)
        )
        await self.db.commit()

        reset = await replay_failed_events(self.db)
        self.assertEqual(reset, 0)
        stored = (await self.db.execute(select(DomainEvent.status))).scalar_one()
        self.assertEqual(stored, "failed")

    async def test_replay_then_dispatch_does_not_duplicate_notifications(self):
        event = DomainEvent(
            event_type="resource.version.submitted",
            subject_type="resource_version",
            subject_id=self.version.id,
            payload={},
            status="failed",
            attempts=1,
        )
        self.db.add(event)
        await self.db.commit()
        event_id = event.id

        reset = await replay_failed_events(self.db)
        await self.db.commit()
        self.assertEqual(reset, 1)

        # dispatch_event 用独立会话；复用测试工厂以指向同一内存库。
        with patch.object(notification_service, "async_session_factory", self.factory):
            await notification_service.dispatch_event(self.db, event_id)
            await notification_service.dispatch_event(self.db, event_id)

        count = await self.db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == self.owner.id
            )
        )
        self.assertEqual(count, 1)  # 重复消费不产生重复通知


if __name__ == "__main__":
    unittest.main()
