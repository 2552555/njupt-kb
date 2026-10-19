"""Beta6 contracts: contributor profile, public author info and owner filter.

数据库测试使用隔离的内存 SQLite，不代表 PostgreSQL 联调结果。
"""

import unittest

from sqlalchemy import select

from app.modules.content import service as content
from app.modules.creator import service as creator
from app.modules.identity import service as identity
import test_content_workflow as workflow


class Beta6DatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = workflow.ContentWorkflowTests.asyncSetUp
    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def publish(self, resource, version):
        from app.modules.access.models import AccessPolicy

        resource.publication_status = "published"
        resource.published_version_id = version.id
        version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=resource.id, metadata_scope="public"))
        await self.db.commit()
        # commit 后服务端生成的 updated_at 会被标记过期；异步下不能隐式懒加载，先显式重载。
        await self.db.refresh(resource)
        await self.db.refresh(version)

    async def test_resource_detail_exposes_owner_and_list_filters_by_owner(self):
        from app.modules.content.models import Resource, ResourceVersion
        from app.modules.identity.models import UserProfile

        self.db.add(UserProfile(user_id=self.owner.id, display_name="梦夜十六"))
        await self.db.flush()
        await self.publish(self.resource, self.version)

        other_resource = Resource(
            course_id=self.course.id, owner_id=self.other.id, resource_type="exam"
        )
        self.db.add(other_resource)
        await self.db.flush()
        other_version = ResourceVersion(
            resource_id=other_resource.id,
            created_by=self.other.id,
            version_no=1,
            title="他人的试卷",
            review_status="draft",
        )
        self.db.add(other_version)
        await self.db.flush()
        await self.publish(other_resource, other_version)

        detail = await content.get_resource_detail(self.db, user=None, resource_id=self.resource.id)
        self.assertEqual(detail.owner_id, self.owner.id)
        self.assertEqual(detail.owner_display_name, "梦夜十六")

        page = await content.list_resources(self.db, user=None, owner_id=self.other.id)
        self.assertEqual([item.id for item in page.items], [other_resource.id])

    async def test_creator_profile_update_clear_and_visibility(self):
        from app.modules.identity.models import UserProfile
        from app.modules.moderation.models import AuditLog

        self.db.add(UserProfile(user_id=self.owner.id, display_name="梦夜十六"))
        await self.db.flush()
        user = await identity.get_user(self.db, self.owner.id)

        result = await creator.update_creator_profile(
            self.db,
            user=user,
            payload={"headline": "南邮资料整理", "bio": "分享课程笔记与复习资料", "is_public": True},
            request_id="beta6",
        )
        self.assertEqual(result.display_name, "梦夜十六")
        self.assertEqual(result.headline, "南邮资料整理")
        self.assertEqual(result.bio, "分享课程笔记与复习资料")
        self.assertTrue(result.contribution.is_creator)

        # 显式 null 只清空该字段，其它字段保持不变。
        result = await creator.update_creator_profile(
            self.db, user=user, payload={"headline": None}, request_id="beta6"
        )
        self.assertIsNone(result.headline)
        self.assertEqual(result.bio, "分享课程笔记与复习资料")

        await self.db.flush()
        audited = (
            await self.db.execute(
                select(AuditLog).where(AuditLog.action == "creator.profile_updated")
            )
        ).scalars().all()
        self.assertTrue(audited)

        # 关闭公开后，公开资料只回退到账号显示名，不泄露简介。
        await creator.update_creator_profile(
            self.db, user=user, payload={"is_public": False}, request_id="beta6"
        )
        public = await creator.get_creator_profile(self.db, self.owner.id)
        self.assertEqual(public.display_name, "梦夜十六")
        self.assertIsNone(public.headline)
        self.assertIsNone(public.bio)

    async def test_owner_filter_over_http(self):
        import httpx

        from app.db import get_db
        from app.main import app

        await self.publish(self.resource, self.version)

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
            ) as client:
                mine = await client.get(f"/api/v1/resources?owner_id={self.owner.id}")
                self.assertEqual(mine.status_code, 200, mine.text)
                self.assertEqual([item["id"] for item in mine.json()["items"]], [str(self.resource.id)])

                # 过滤不扩大可见范围：他人没有公开资料时返回空列表，而不是 403/500。
                theirs = await client.get(f"/api/v1/resources?owner_id={self.other.id}")
                self.assertEqual(theirs.status_code, 200, theirs.text)
                self.assertEqual(theirs.json()["items"], [])
        finally:
            app.dependency_overrides.clear()

    async def test_creator_profile_endpoint_requires_login(self):
        import httpx

        from app.config import settings
        from app.db import get_db
        from app.main import app

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
                response = await client.put(
                    "/api/v1/me/creator-profile", json={"headline": "未登录"}
                )
                self.assertEqual(response.status_code, 401, response.text)
        finally:
            app.dependency_overrides.clear()
