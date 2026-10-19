"""Beta7 推荐与攻略契约，使用隔离 SQLite，不证明生产依赖联调。"""

import unittest
from datetime import datetime, timedelta, timezone

from app.modules.content import service as content
from app.modules.content.models import Resource, ResourceVersion
from app.modules.access.models import AccessPolicy
from app.modules.interaction.models import Favorite, Follow, Rating
from app.modules.interaction.recommendations import recommend_resources
import test_content_workflow as workflow


class Beta7Tests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = workflow.ContentWorkflowTests.asyncSetUp
    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def add_resource(self, *, kind="guide", scope="public", freshness="current", published=True):
        resource = Resource(course_id=self.course.id, owner_id=self.owner.id,
                            resource_type=kind, freshness_status=freshness,
                            publication_status="published" if published else "unpublished")
        self.db.add(resource)
        await self.db.flush()
        version = ResourceVersion(resource_id=resource.id, created_by=self.owner.id,
                                  version_no=1, title=f"测试{kind}", review_status="approved")
        self.db.add(version)
        await self.db.flush()
        resource.published_version_id = version.id
        self.db.add(AccessPolicy(resource_id=resource.id, metadata_scope=scope))
        await self.db.flush()
        return resource, version

    async def test_visibility_is_applied_before_limit(self):
        await self.add_resource(scope="private")
        await self.add_resource(published=False)
        await self.add_resource(freshness="outdated")
        visible, _ = await self.add_resource()
        result = await recommend_resources(self.db, limit=1)
        self.assertEqual([item.resource_id for item in result.items], [visible.id])
        self.assertIn("现行内容", result.items[0].reasons)

    async def test_followed_creator_and_unfollow(self):
        from sqlalchemy import delete

        older, _ = await self.add_resource()
        newer, _ = await self.add_resource()
        newer.owner_id = self.other.id
        newer.updated_at = datetime.now(timezone.utc)
        older.updated_at = newer.updated_at - timedelta(days=1)
        follow = Follow(user_id=self.other.id, target_type="creator", target_id=self.owner.id)
        self.db.add(follow)
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=1)
        self.assertEqual(result.items[0].resource_id, older.id)
        self.assertIn("来自你关注的贡献者", result.items[0].reasons)
        await self.db.execute(delete(Follow).where(Follow.id == follow.id))
        result = await recommend_resources(self.db, user=self.other, limit=1)
        self.assertEqual(result.items[0].resource_id, newer.id)

    async def test_shrunk_ratings_precede_favorites(self):
        single, single_version = await self.add_resource()
        established, established_version = await self.add_resource()
        # 同一账号跨版本评价允许存在；本测试用独立历史版本构造样本量。
        self.db.add(Rating(user_id=self.owner.id, resource_id=single.id,
                           version_id=single_version.id, score=5))
        for index in range(5):
            version = established_version
            if index:
                version = ResourceVersion(resource_id=established.id, created_by=self.owner.id,
                                          version_no=index + 1, title="历史", review_status="approved")
                self.db.add(version)
                await self.db.flush()
            self.db.add(Rating(user_id=self.owner.id, resource_id=established.id,
                               version_id=version.id, score=5))
        self.db.add(Favorite(user_id=self.owner.id, resource_id=single.id))
        await self.db.flush()
        result = await recommend_resources(self.db)
        self.assertEqual(result.items[0].resource_id, established.id)
        self.assertGreater(result.items[0].rating.shrunk, result.items[1].rating.shrunk)

    async def test_course_filter_and_guides_use_published_version(self):
        from app.modules.catalog.models import Course

        guide, version = await self.add_resource()
        await self.add_resource(kind="note")
        other_course = Course(university_id=self.school.id, name="另一课程", code="T2")
        self.db.add(other_course)
        await self.db.flush()
        elsewhere, _ = await self.add_resource()
        elsewhere.course_id = other_course.id
        self.db.add(ResourceVersion(resource_id=guide.id, created_by=self.owner.id,
                                    version_no=2, title="未审核的新攻略", review_status="draft"))
        await self.db.flush()
        page = await content.list_resources(self.db, user=None, course_id=self.course.id,
                                            resource_type="guide")
        self.assertEqual([item.id for item in page.items], [guide.id])
        self.assertEqual(page.items[0].title, version.title)
        recommendations = await recommend_resources(self.db, course_id=self.course.id)
        self.assertTrue(all(item.course_id == self.course.id for item in recommendations.items))
        self.assertTrue(all("这门课的资料" in item.reasons for item in recommendations.items))

    async def test_endpoint_validation_and_restricted_account(self):
        import httpx
        from app.db import get_db
        from app.main import app

        await self.add_resource()
        self.other.status = "pending_verification"
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other)
        self.assertEqual(result.items, [])
        # 暂停下载的账号仍可按既有权限读取元数据，推荐不得改变这条规则。
        self.other.status = "suspended"
        await self.db.flush()
        self.assertEqual(len((await recommend_resources(self.db, user=self.other)).items), 1)

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000") as client:
                response = await client.get("/api/v1/recommendations?limit=1")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()["items"]), 1)
                for query in ("limit=0", "limit=21", "course_id=invalid"):
                    self.assertEqual((await client.get(f"/api/v1/recommendations?{query}")).status_code, 422)
        finally:
            app.dependency_overrides.clear()

    async def test_followed_course_and_visibility_changes_are_immediate(self):
        from sqlalchemy import select
        from app.modules.catalog.models import Course

        followed, _ = await self.add_resource()
        other_course = Course(university_id=self.school.id, name="未关注课程", code="T3")
        self.db.add(other_course)
        await self.db.flush()
        recent, _ = await self.add_resource()
        recent.course_id = other_course.id
        recent.updated_at = datetime.now(timezone.utc)
        followed.updated_at = recent.updated_at - timedelta(days=1)
        self.db.add(Follow(user_id=self.other.id, target_type="course", target_id=self.course.id))
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=1)
        self.assertEqual(result.items[0].resource_id, followed.id)
        self.assertIn("来自你关注的课程", result.items[0].reasons)
        policy = await self.db.scalar(select(AccessPolicy).where(AccessPolicy.resource_id == followed.id))
        policy.metadata_scope = "private"
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=1)
        self.assertEqual(result.items[0].resource_id, recent.id)
        recent.publication_status = "withdrawn"
        await self.db.flush()
        self.assertEqual((await recommend_resources(self.db, user=self.other)).items, [])
