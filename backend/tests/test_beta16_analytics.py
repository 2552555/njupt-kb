"""beta16 管理端数据分析看板与审计导出测试（隔离 SQLite）。

覆盖：
- ``GET /admin/analytics/overview`` 需要 ``metrics.view``（无权 403）、窗口长度与日期连续性、
  days 越界报输入错误、当日数据进入对应桶、totals 等于 series 各桶之和、漏斗非负、
  top_courses 排序与条数、data_notes 说明边界；
- ``GET /admin/audit/export`` 需要 ``audit.view``（无权 403）、CSV 带 BOM 与表头、
  行数正确、超过上限截断并标注响应头、导出动作写入 ``audit.export`` 审计、
  CSV 公式注入前缀被单引号转义。

只验证端点契约与聚合口径，不宣称生产环境并发或时区结论（测试库为 SQLite）。
"""

import csv
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, select

from app.config import settings
from app.modules.access.models import DownloadGrant
from app.modules.catalog.models import Course
from app.modules.content.models import Resource, ResourceFile
from app.modules.identity.models import User
from app.modules.interaction.models import Comment, Favorite
from app.modules.moderation.models import AuditLog

import test_content_workflow as workflow

ANALYTICS_METRICS = (
    "new_users",
    "new_resources",
    "published_versions",
    "downloads",
    "favorites",
    "ratings",
    "comments",
    "reports",
    "corrections",
    "requests",
    "cases",
)


class Beta16AnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="analytics-admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.db.add(self.admin)
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

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

    async def _overview(self, days=None, user=None):
        """以指定身份请求分析总览，返回 httpx.Response。"""
        from app.main import app

        await self._override(user or self.admin)
        try:
            params = {} if days is None else {"days": days}
            async with self._client() as client:
                return await client.get("/api/v1/admin/analytics/overview", params=params)
        finally:
            app.dependency_overrides.clear()

    async def _export(self, params=None, user=None):
        from app.main import app

        await self._override(user or self.admin)
        try:
            async with self._client() as client:
                return await client.get("/api/v1/admin/audit/export", params=params or {})
        finally:
            app.dependency_overrides.clear()

    def _audit(self, action="test.action", object_type="resource", **kwargs):
        return AuditLog(action=action, object_type=object_type, **kwargs)

    def _grant(self, resource_id, key, user_id=None):
        return DownloadGrant(
            user_id=user_id or self.owner.id,
            resource_id=resource_id,
            version_id=self.version.id,
            file_id=self.file.id,
            idempotency_key=key,
            request_hash="0" * 64,
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        )

    async def _file(self):
        self.file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key="beta16/file.pdf",
            original_name="file.pdf",
            size=1024,
        )
        self.db.add(self.file)
        await self.db.commit()

    # --- 分析总览：权限与参数 -------------------------------------------------

    async def test_analytics_requires_metrics_view(self):
        response = await self._overview(days=7, user=self.owner)
        self.assertEqual(response.status_code, 403, response.text)

    async def test_series_has_requested_length_and_contiguous_dates(self):
        response = await self._overview(days=14)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["window_days"], 14)
        self.assertEqual(len(body["series"]), 14)

        dates = [datetime.fromisoformat(point["date"]).date() for point in body["series"]]
        for previous, current in zip(dates, dates[1:]):
            self.assertEqual((current - previous).days, 1)
        # 窗口以今天（UTC）结束，日期升序。
        self.assertEqual(dates[-1], datetime.now(timezone.utc).date())

    async def test_days_over_max_rejected(self):
        response = await self._overview(days=settings.ANALYTICS_MAX_WINDOW_DAYS + 1)
        self.assertIn(response.status_code, (400, 422), response.text)

    async def test_days_non_positive_rejected(self):
        for days in (0, -3):
            response = await self._overview(days=days)
            self.assertIn(response.status_code, (400, 422), response.text)

    # --- 分析总览：口径 -------------------------------------------------------

    async def test_today_bucket_counts_new_favorite_and_comment(self):
        self.db.add_all(
            [
                Favorite(user_id=self.other.id, resource_id=self.resource.id),
                Comment(resource_id=self.resource.id, user_id=self.other.id, body="good"),
            ]
        )
        await self.db.commit()

        response = await self._overview(days=7)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        today = datetime.now(timezone.utc).date().isoformat()
        bucket = next(point for point in body["series"] if point["date"] == today)

        self.assertEqual(bucket["favorites"], 1)
        self.assertEqual(bucket["comments"], 1)
        # 三个账号（owner/other/admin）均为当天新建。
        self.assertEqual(bucket["new_users"], 3)

    async def test_totals_equal_series_sums(self):
        await self._file()
        self.db.add_all([self._grant(self.resource.id, "k1")])
        await self.db.commit()

        response = await self._overview(days=10)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ANALYTICS_METRICS:
            self.assertEqual(
                body["totals"][key],
                sum(point[key] for point in body["series"]),
                key,
            )
        self.assertEqual(body["totals"]["downloads"], 1)

    async def test_funnel_stages_are_non_negative(self):
        response = await self._overview(days=14)
        self.assertEqual(response.status_code, 200, response.text)
        funnel = response.json()["funnel"]
        self.assertEqual(
            set(funnel),
            {"uploaded", "submitted", "approved", "published", "downloaded"},
        )
        for key, value in funnel.items():
            self.assertIsInstance(value, int, key)
            self.assertGreaterEqual(value, 0, key)

    async def test_top_courses_sorted_and_limited(self):
        await self._file()
        second_course = Course(university_id=self.school.id, name="Second course")
        self.db.add(second_course)
        await self.db.flush()
        second_resource = Resource(
            course_id=second_course.id, owner_id=self.owner.id, resource_type="note"
        )
        self.db.add(second_resource)
        await self.db.flush()
        self.db.add_all(
            [
                self._grant(self.resource.id, "k1"),
                self._grant(self.resource.id, "k2"),
                self._grant(second_resource.id, "k3"),
            ]
        )
        await self.db.commit()

        response = await self._overview(days=7)
        self.assertEqual(response.status_code, 200, response.text)
        top = response.json()["top_courses"]

        self.assertLessEqual(len(top), 10)
        counts = [item["download_count"] for item in top]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(counts[0], 2)
        self.assertEqual(
            set(top[0]),
            {"course_id", "name", "download_count"},
        )

    async def test_data_notes_explain_boundaries(self):
        response = await self._overview(days=7)
        self.assertEqual(response.status_code, 200, response.text)
        notes = response.json()["data_notes"]
        self.assertTrue(notes)
        joined = "".join(notes)
        self.assertTrue("只读" in joined or "未纳入" in joined)

    # --- 审计导出 -------------------------------------------------------------

    async def test_audit_export_requires_audit_view(self):
        response = await self._export(user=self.owner)  # contributor：无 audit.view
        self.assertEqual(response.status_code, 403, response.text)

    async def test_audit_export_csv_has_bom_header_and_rows(self):
        self.db.add_all([self._audit(action=f"test.action{i}") for i in range(3)])
        await self.db.commit()

        response = await self._export()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(response.text.startswith("\ufeffcreated_at,action,"))

        reader = csv.reader(io.StringIO(response.text.lstrip("\ufeff")))
        rows = list(reader)
        self.assertEqual(
            rows[0],
            [
                "created_at",
                "action",
                "actor_email",
                "actor_role",
                "target_type",
                "target_id",
                "request_id",
            ],
        )
        self.assertEqual(len(rows), 1 + 3)
        # 不导出变更值原文：表头与数据行都不含 before/after。
        self.assertNotIn("before", response.text)
        self.assertNotIn("after", response.text)

    async def test_audit_export_truncates_at_max_rows(self):
        self.db.add_all([self._audit(action=f"test.action{i}") for i in range(3)])
        await self.db.commit()

        with patch.object(settings, "AUDIT_EXPORT_MAX_ROWS", 2):
            response = await self._export()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-export-truncated"], "true")
        rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
        self.assertEqual(len(rows) - 1, 2)

    async def test_audit_export_writes_audit_record(self):
        await self._export(params={"action": "test."})
        count = await self.db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "audit.export")
        )
        self.assertEqual(count, 1)

        record = (
            await self.db.execute(
                select(AuditLog).where(AuditLog.action == "audit.export")
            )
        ).scalar_one()
        self.assertEqual(record.actor_id, self.admin.id)
        self.assertEqual(record.object_type, "audit_log")
        self.assertEqual(record.after_state["action_prefix"], "test.")
        self.assertIn("rows", record.after_state)
        self.assertIn("truncated", record.after_state)

    async def test_audit_export_escapes_formula_prefixes(self):
        self.db.add(
            self._audit(
                action="test.injection",
                object_type="=1+1",
                object_id="+2",
                request_id="@cmd",
            )
        )
        await self.db.commit()

        response = await self._export()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("'=1+1", response.text)
        self.assertIn("'+2", response.text)
        self.assertIn("'@cmd", response.text)


if __name__ == "__main__":
    unittest.main()
