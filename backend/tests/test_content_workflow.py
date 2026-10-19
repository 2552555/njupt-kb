"""Transactional content tests on an isolated SQLite database.

JSONB is compiled as JSON only in these tests. PostgreSQL concurrency and migrations
still need the Docker integration environment; these tests do not certify them.
"""

import unittest
from unittest.mock import patch

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models_registry  # noqa: F401
from app.db import Base
from app.core.errors import NotFound
from app.core.enums import CorrectionStatus
from app.modules.identity.models import User
from app.modules.catalog.models import Course, University
from app.modules.content.models import Resource, ResourceVersion, RightsDeclaration
from app.modules.access.models import AccessPolicy
from app.modules.content import service as content
from app.modules.content import management
from app.modules.content.schemas import VersionCreate, VersionUpdate


@compiles(JSONB, "sqlite")
def jsonb_for_test(type_, compiler, **kwargs):
    return "JSON"


class ContentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.owner = User(
            email="owner@example.test",
            password_hash="unused",
            role="contributor",
            status="active",
        )
        self.other = User(
            email="other@example.test",
            password_hash="unused",
            role="contributor",
            status="active",
        )
        self.school = University(name="Test school", slug="test")
        self.db.add_all([self.owner, self.other, self.school])
        await self.db.flush()
        self.course = Course(
            university_id=self.school.id, name="Test course", code="T1"
        )
        self.db.add(self.course)
        await self.db.flush()
        self.resource = Resource(
            course_id=self.course.id, owner_id=self.owner.id, resource_type="note"
        )
        self.db.add(self.resource)
        await self.db.flush()
        self.version = ResourceVersion(
            resource_id=self.resource.id,
            created_by=self.owner.id,
            version_no=1,
            title="private test",
            review_status="draft",
        )
        self.db.add(self.version)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(self.resource)
        await self.db.refresh(self.version)

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def publish_private(self):
        self.version.review_status = "approved"
        self.resource.published_version_id = self.version.id
        self.resource.publication_status = "published"
        self.db.add(
            AccessPolicy(resource_id=self.resource.id, metadata_scope="private")
        )
        await self.db.commit()

    async def test_private_published_content_is_excluded_from_public_list(self):
        await self.publish_private()
        page = await content.list_resources(self.db, user=None)
        self.assertEqual(page.items, [])

    async def test_owner_can_find_private_published_content(self):
        await self.publish_private()
        page = await content.list_resources(self.db, user=self.owner)
        self.assertEqual([item.id for item in page.items], [self.resource.id])

    async def test_my_resources_includes_drafts_but_not_other_authors(self):
        page = await management.list_my_resources(self.db, user=self.owner)
        self.assertEqual(page.items[0].latest_version.id, self.version.id)
        self.assertEqual(
            (await management.list_my_resources(self.db, user=self.other)).items, []
        )

    async def test_owner_can_read_draft_snapshot_and_rights(self):
        self.db.add(
            RightsDeclaration(
                version_id=self.version.id,
                declarant_id=self.owner.id,
                rights_basis="original",
                declaration_text_version="v1",
            )
        )
        await self.db.commit()
        detail = await management.get_version_detail(
            self.db,
            user=self.owner,
            resource_id=self.resource.id,
            version_id=self.version.id,
        )
        self.assertEqual(detail.version.id, self.version.id)
        self.assertEqual(detail.rights.rights_basis, "original")

    async def test_other_author_cannot_read_draft_snapshot(self):
        with self.assertRaises(NotFound):
            await management.get_version_detail(
                self.db,
                user=self.other,
                resource_id=self.resource.id,
                version_id=self.version.id,
            )

    async def test_metadata_does_not_require_a_scanned_file(self):
        self.resource.published_version_id = self.version.id
        self.resource.publication_status = "published"
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        await self.db.commit()
        await self.db.refresh(self.resource)
        await self.db.refresh(self.version)
        detail = await content.get_resource_detail(
            self.db, user=None, resource_id=self.resource.id
        )
        self.assertEqual(detail.published_version.title, "private test")

    async def test_private_search_is_filtered_before_pagination(self):
        from app.modules.interaction.service import search

        await self.publish_private()
        result = await search(
            self.db,
            user=None,
            q=None,
            university_id=None,
            curriculum_id=None,
            course_id=None,
            semester_id=None,
            teacher_id=None,
            resource_type=None,
            applicable_year=None,
            cursor=None,
            limit=20,
        )
        self.assertEqual(result.items, [])

    async def publish_public(self, title: str = "public note"):
        self.version.title = title
        self.version.review_status = "approved"
        self.resource.published_version_id = self.version.id
        self.resource.publication_status = "published"
        self.db.add(
            AccessPolicy(resource_id=self.resource.id, metadata_scope="public")
        )
        await self.db.commit()

    async def test_keyword_search_finds_published_public_resource(self):
        """带 q 的搜索要在 SQLite（本地开发/测试库）上同样可用。

        回归：搜索 SQL 原先用 PostgreSQL 专有的 ILIKE / EXTRACT，在 SQLite 上直接
        语法错误（500），导致本地界面搜索不可用。测试库是 SQLite，这条用例会守住它。
        """
        from app.modules.interaction.service import search

        await self.publish_public(title="数据结构复习笔记")
        result = await search(
            self.db,
            user=None,
            q="复习",
            university_id=None,
            curriculum_id=None,
            course_id=None,
            semester_id=None,
            teacher_id=None,
            resource_type=None,
            applicable_year=None,
            cursor=None,
            limit=20,
        )
        self.assertEqual([item.title for item in result.items], ["数据结构复习笔记"])

    async def test_keyword_search_without_match_returns_empty(self):
        from app.modules.interaction.service import search

        await self.publish_public(title="数据结构复习笔记")
        result = await search(
            self.db,
            user=None,
            q="操作系统",
            university_id=None,
            curriculum_id=None,
            course_id=None,
            semester_id=None,
            teacher_id=None,
            resource_type=None,
            applicable_year=None,
            cursor=None,
            limit=20,
        )
        self.assertEqual(result.items, [])

    async def test_keyword_search_matches_course_name_with_uuid_params(self):
        """q 命中课程名时要展开课程 ID 列表作为查询参数。

        回归：裸 SQL 的 UUID 参数此前未声明类型，SQLite 的 DBAPI 拒绝 uuid.UUID
        对象（Error binding parameter: type 'UUID' is not supported），而 PostgreSQL
        的 psycopg 会自适应，所以只有本地/测试库上必现。
        """
        from app.modules.catalog.models import Course as CourseModel
        from app.modules.content.models import Resource, ResourceVersion
        from app.modules.interaction.service import search
        from sqlalchemy import select

        other_course = CourseModel(
            university_id=self.school.id, name="数据结构导论", code="DS1"
        )
        self.db.add(other_course)
        await self.db.flush()
        other_resource = Resource(
            course_id=other_course.id, owner_id=self.owner.id, resource_type="note"
        )
        self.db.add(other_resource)
        await self.db.flush()
        self.db.add(
            ResourceVersion(
                resource_id=other_resource.id,
                created_by=self.owner.id,
                version_no=1,
                title="期中试卷",
                review_status="approved",
            )
        )
        await self.db.flush()
        other_resource.published_version_id = (
            await self.db.execute(
                select(ResourceVersion.id).where(
                    ResourceVersion.resource_id == other_resource.id
                )
            )
        ).scalar_one()
        other_resource.publication_status = "published"
        self.db.add(
            AccessPolicy(resource_id=other_resource.id, metadata_scope="public")
        )
        await self.db.commit()

        result = await search(
            self.db,
            user=None,
            q="数据结构",
            university_id=None,
            curriculum_id=None,
            course_id=None,
            semester_id=None,
            teacher_id=None,
            resource_type=None,
            applicable_year=None,
            cursor=None,
            limit=20,
        )
        self.assertEqual([item.title for item in result.items], ["期中试卷"])

    async def test_applicable_year_filter_matches_semester_start_date(self):
        """按适用年份筛选同样要跨方言可用（PostgreSQL EXTRACT / SQLite strftime）。"""
        from datetime import date

        from sqlalchemy import select

        from app.modules.catalog.models import CourseOffering, Semester
        from app.modules.interaction.service import search

        await self.publish_public(title="2025 秋季笔记")
        semester = Semester(
            university_id=self.school.id,
            academic_year="2025-2026",
            season="fall",
            start_date=date(2025, 9, 1),
            end_date=date(2026, 1, 15),
        )
        self.db.add(semester)
        await self.db.flush()
        self.db.add(
            CourseOffering(course_id=self.course.id, semester_id=semester.id)
        )
        await self.db.flush()
        self.resource.offering_id = (
            await self.db.execute(
                select(CourseOffering.id).where(
                    CourseOffering.course_id == self.course.id,
                    CourseOffering.semester_id == semester.id,
                )
            )
        ).scalar_one()
        await self.db.commit()

        result = await search(
            self.db,
            user=None,
            q=None,
            university_id=None,
            curriculum_id=None,
            course_id=None,
            semester_id=None,
            teacher_id=None,
            resource_type=None,
            applicable_year=2025,
            cursor=None,
            limit=20,
        )
        self.assertEqual([item.title for item in result.items], ["2025 秋季笔记"])

        miss = await search(
            self.db,
            user=None,
            q=None,
            university_id=None,
            curriculum_id=None,
            course_id=None,
            semester_id=None,
            teacher_id=None,
            resource_type=None,
            applicable_year=2024,
            cursor=None,
            limit=20,
        )
        self.assertEqual(miss.items, [])

    async def test_rejected_version_can_be_revised_without_overwriting_history(self):
        self.version.review_status = "rejected"
        await self.db.commit()
        await self.db.refresh(self.version)
        revision = await content.create_version(
            self.db,
            user=self.owner,
            resource_id=self.resource.id,
            payload=VersionCreate(title="corrected title"),
            request_id="test",
        )
        await self.db.commit()
        self.assertEqual(revision.version_no, 2)
        self.assertEqual(revision.review_status, "draft")
        original = await management.get_version_detail(
            self.db,
            user=self.owner,
            resource_id=self.resource.id,
            version_id=self.version.id,
        )
        self.assertEqual(original.version.title, "private test")
        self.assertEqual(original.version.review_status, "rejected")

    async def test_frozen_version_is_not_editable(self):
        from app.core.errors import Conflict

        self.version.review_status = "pending"
        await self.db.commit()
        with self.assertRaises(Conflict):
            await content.update_version(
                self.db,
                user=self.owner,
                resource_id=self.resource.id,
                version_id=self.version.id,
                payload=VersionUpdate(revision=1, title="overwrite"),
                request_id="test",
            )

    async def test_api_reads_exact_draft_and_denies_other_author(self):
        import httpx
        from app.main import app
        from app.core.deps import get_current_user
        from app.db import get_db

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: self.owner
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                path = (
                    f"/api/v1/resources/{self.resource.id}/versions/{self.version.id}"
                )
                response = await client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["version"]["id"], str(self.version.id))
                app.dependency_overrides[get_current_user] = lambda: self.other
                self.assertEqual((await client.get(path)).status_code, 404)
        finally:
            app.dependency_overrides.clear()

    async def test_reviewer_cannot_edit_another_authors_draft(self):
        self.other.role = "reviewer"
        with self.assertRaises(NotFound):
            await content.update_version(
                self.db,
                user=self.other,
                resource_id=self.resource.id,
                version_id=self.version.id,
                payload=VersionUpdate(revision=1, title="wrong author"),
                request_id="test",
            )

    async def test_reviewer_cannot_inspect_unsubmitted_draft(self):
        self.other.role = "reviewer"
        with self.assertRaises(NotFound):
            await management.get_version_detail(
                self.db,
                user=self.other,
                resource_id=self.resource.id,
                version_id=self.version.id,
            )

    async def test_scan_failure_and_job_failure_commit_together(self):
        from app.modules.content.models import ResourceFile
        from app.modules.jobs.models import Job
        from app.modules.jobs import service as jobs
        from app.core.enums import ScanStatus

        file = ResourceFile(
            uploader_id=self.owner.id, storage_key="test-only", original_name="test.pdf"
        )
        self.db.add(file)
        await self.db.flush()
        file_id = file.id
        job = Job(
            job_type="file.scan", target_id=str(file_id), processor_version="scan/1"
        )
        self.db.add(job)
        await self.db.commit()
        job_id = job.id

        async def reject(db, *, file_id):
            record = await db.get(ResourceFile, file_id)
            record.scan_status = "rejected"
            record.scan_failure_reason = "test rejection"
            await db.flush()
            return ScanStatus.REJECTED

        with (
            patch.object(jobs, "async_session_factory", self.factory),
            patch.object(content, "process_file_scan", reject),
        ):
            self.assertEqual(await jobs.run_job(str(job_id)), "failed")
        async with self.factory() as db:
            self.assertEqual(
                (await db.get(ResourceFile, file_id)).scan_status, "rejected"
            )
            self.assertEqual(
                (await db.get(ResourceFile, file_id)).scan_failure_reason,
                "test rejection",
            )
            self.assertEqual((await db.get(Job, job_id)).status, "failed")

    async def test_new_resource_has_explicit_conservative_access_policy(self):
        from app.modules.content.schemas import ResourceCreate
        created = await content.create_resource(self.db, user=self.owner, request_id="test",
            payload=ResourceCreate(course_id=self.course.id, resource_type="note", title="new resource"))
        from sqlalchemy import select
        policy = await self.db.scalar(select(AccessPolicy).where(AccessPolicy.resource_id == created.resource_id))
        self.assertIsNotNone(policy)
        self.assertEqual(policy.metadata_scope, "public")
        self.assertEqual(policy.preview_scope, "authenticated")
        self.assertEqual(policy.original_scope, "explicit_grant")

    async def test_pdf_preview_is_preferred_over_newer_thumbnail(self):
        from app.modules.content.models import ResourceFile, FileDerivative
        from app.modules.access.service import _find_preview_derivative
        from datetime import datetime, timedelta, timezone

        file = ResourceFile(
            uploader_id=self.owner.id, storage_key="test-only", original_name="test.pdf"
        )
        self.db.add(file)
        await self.db.flush()
        now = datetime.now(timezone.utc)
        self.db.add_all(
            [
                FileDerivative(
                    source_file_id=file.id,
                    processor_version="derive/1",
                    kind="preview_pdf",
                    status="ready",
                    storage_key="correct-pdf",
                    created_at=now,
                ),
                FileDerivative(
                    source_file_id=file.id,
                    processor_version="derive/1",
                    kind="thumbnail",
                    status="ready",
                    storage_key="thumbnail",
                    created_at=now + timedelta(seconds=1),
                ),
            ]
        )
        await self.db.commit()
        result = await _find_preview_derivative(self.db, source_file_id=file.id)
        self.assertEqual(result[1], "correct-pdf")

    async def test_admin_correction_list_filters_by_status(self):
        from app.modules.interaction import service as interaction
        from app.modules.interaction.models import Correction

        self.db.add_all(
            [
                Correction(
                    resource_id=self.resource.id,
                    version_id=self.version.id,
                    submitter_id=self.other.id,
                    description="页码错了",
                ),
                Correction(
                    resource_id=self.resource.id,
                    version_id=self.version.id,
                    submitter_id=self.other.id,
                    description="封面错了",
                    status="fixed",
                ),
            ]
        )
        await self.db.commit()

        all_page = await interaction.list_corrections(self.db, status=None, cursor=None, limit=None)
        self.assertEqual(len(all_page.items), 2)

        open_page = await interaction.list_corrections(
            self.db, status=CorrectionStatus.OPEN, cursor=None, limit=None
        )
        self.assertEqual([item.description for item in open_page.items], ["页码错了"])


if __name__ == "__main__":
    unittest.main()
