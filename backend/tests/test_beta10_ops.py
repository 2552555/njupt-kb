"""Beta10 目录维护与运营任务工作台契约测试（隔离 SQLite；不代表生产并发已验证）。"""

import unittest
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.errors import InvalidInput
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import (
    CatalogCreateIn,
    CatalogDeactivateIn,
    CatalogEntityType,
    CatalogRenameIn,
)
from app.modules.content import service as content
from app.modules.moderation.models import AuditLog, Report, Review
from app.modules.ops import service as ops
import test_content_workflow as workflow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Beta10OpsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 公开一门课程下的一份已发布资料，用于验证「停用课程后资料仍可读」。
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        from app.modules.access.models import AccessPolicy

        self.db.add(
            AccessPolicy(resource_id=self.resource.id, metadata_scope="public")
        )
        self.admin = self.owner
        self.admin.role = "admin"
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # --- 权限 ---------------------------------------------------------------

    async def test_non_admin_is_forbidden_on_catalog_and_workbench(self):
        import httpx
        from app.config import settings
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        async def database():
            yield self.db

        async def contributor():
            return self.other

        async def admin():
            return self.admin

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                app.dependency_overrides[get_current_user] = contributor
                self.assertEqual(
                    (await client.get("/api/v1/admin/catalog/course")).status_code, 403
                )
                self.assertEqual(
                    (await client.get("/api/v1/admin/workbench")).status_code, 403
                )
                app.dependency_overrides[get_current_user] = admin
                self.assertEqual(
                    (await client.get("/api/v1/admin/catalog/course")).status_code, 200
                )
                self.assertEqual(
                    (await client.get("/api/v1/admin/workbench")).status_code, 200
                )
        finally:
            app.dependency_overrides.clear()

    # --- 停用 ---------------------------------------------------------------

    async def test_deactivated_course_hides_from_frontend_but_resource_stays_readable(
        self,
    ):
        await catalog.admin_deactivate_catalog(
            self.db,
            CatalogEntityType.COURSE,
            self.course.id,
            CatalogDeactivateIn(reason="课程合并前的停用"),
            actor=self.admin,
            request_id="beta10",
        )
        await self.db.commit()

        page = await catalog.list_courses(
            self.db,
            keyword=None,
            filters=catalog.CourseSearchFilters(),
            limit=None,
            cursor=None,
        )
        self.assertNotIn(self.course.id, [item.id for item in page.items])

        # 管理员列表仍可见（用于恢复/维护）。
        admin_items = await catalog.admin_list_catalog(
            self.db, CatalogEntityType.COURSE
        )
        deactivated = [item for item in admin_items if item.id == self.course.id]
        self.assertEqual(len(deactivated), 1)
        self.assertEqual(deactivated[0].status, "inactive")

        # 已关联资料仍可正常读取（停用不连带撤下资料）。
        await self.db.refresh(self.resource)
        await self.db.refresh(self.version)
        detail = await content.get_resource_detail(
            self.db, user=None, resource_id=self.resource.id
        )
        self.assertEqual(detail.published_version.title, "private test")

    async def test_rename_and_create_require_valid_parent(self):
        await catalog.admin_rename_catalog(
            self.db,
            CatalogEntityType.COURSE,
            self.course.id,
            CatalogRenameIn(name="改名后的课程", reason="目录修正"),
            actor=self.admin,
            request_id="beta10",
        )
        await self.db.refresh(self.course)
        self.assertEqual(self.course.name, "改名后的课程")

        with self.assertRaises(InvalidInput):
            await catalog.admin_create_catalog(
                self.db,
                CatalogEntityType.COLLEGE,
                CatalogCreateIn(name="无学校学院"),
                actor=self.admin,
                request_id="beta10",
            )

    # --- 合并影响面 ---------------------------------------------------------

    async def test_merge_reports_impact_and_reassigns_references(self):
        from app.modules.catalog.models import Course

        target = Course(
            university_id=self.school.id, name="目标课程", code="T2"
        )
        self.db.add(target)
        await self.db.flush()

        impact = await catalog.admin_merge_impact(
            self.db, CatalogEntityType.COURSE, self.course.id, target.id
        )
        self.assertEqual(impact.total, 1)  # 仅一份资料引用源课程
        resources_row = next(r for r in impact.references if r.table == "resources")
        self.assertEqual(resources_row.count, 1)

        result = await catalog.admin_merge_catalog(
            self.db,
            CatalogEntityType.COURSE,
            self.course.id,
            target.id,
            "同一门课",
            actor=self.admin,
            request_id="beta10",
        )
        self.assertEqual(result.total, 1)
        await self.db.commit()
        await self.db.refresh(self.resource)
        await self.db.refresh(self.course)
        self.assertEqual(self.resource.course_id, target.id)
        self.assertEqual(self.course.status, "merged")
        audit_count = await self.db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "catalog.merge")
        )
        self.assertEqual(audit_count, 1)

    # --- 工作台计数 ---------------------------------------------------------

    async def test_workbench_counts_match_database(self):
        from app.modules.interaction.models import Correction, ResourceRequest
        from app.modules.jobs.models import Job
        from app.modules.notification.models import DomainEvent

        self.db.add_all(
            [
                Review(
                    resource_id=self.resource.id,
                    version_id=self.version.id,
                    submitter_id=self.owner.id,
                    status="pending",
                    snapshot_revision=1,
                    submitted_at=_now(),
                ),
                Correction(
                    resource_id=self.resource.id,
                    version_id=self.version.id,
                    submitter_id=self.other.id,
                    description="页码错了",
                    status="open",
                ),
                Report(
                    resource_id=self.resource.id,
                    reporter_id=self.other.id,
                    reason_code="copyright",
                    status="open",
                ),
                ResourceRequest(
                    requester_id=self.other.id,
                    course_id=self.course.id,
                    description="需要课程笔记",
                    status="open",
                    dedupe_key="beta10-dedupe",
                ),
                DomainEvent(
                    event_type="request.created",
                    subject_type="resource_request",
                    subject_id=self.resource.id,
                    payload={},
                    status="failed",
                ),
                Job(
                    job_type="file.scan",
                    target_id=str(self.resource.id),
                    processor_version="scan/1",
                    status="failed",
                ),
            ]
        )
        await self.db.commit()

        summary = await ops.workbench_summary(self.db)
        counts = {item.key: item.count for item in summary.items}
        self.assertEqual(counts["pending_reviews"], 1)
        self.assertEqual(counts["open_corrections"], 1)
        self.assertEqual(counts["open_reports"], 1)
        self.assertEqual(counts["open_requests"], 1)
        self.assertEqual(counts["failed_tasks"], 2)  # 1 个失败事件 + 1 个失败任务
        self.assertEqual(summary.total, 6)

        # 与数据库逐项核对，避免聚合口径漂移。
        self.assertEqual(
            counts["pending_reviews"],
            await self.db.scalar(
                select(func.count(Review.id)).where(Review.status == "pending")
            ),
        )
        self.assertEqual(
            counts["open_corrections"],
            await self.db.scalar(
                select(func.count(Correction.id)).where(Correction.status == "open")
            ),
        )
        self.assertEqual(
            counts["failed_tasks"],
            await self.db.scalar(
                select(func.count(DomainEvent.id)).where(DomainEvent.status == "failed")
            )
            + await self.db.scalar(
                select(func.count(Job.id)).where(Job.status == "failed")
            ),
        )


if __name__ == "__main__":
    unittest.main()
