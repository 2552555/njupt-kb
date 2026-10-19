"""Beta14 计数展示：课程分类计数与资源详情计数（隔离 SQLite；不宣称生产并发结论）。

覆盖：
- 课程详情 resource_counts 只统计已发布且对当前查看者可见的资源，私有资源既不计入
  也不以计数形式泄露存在性（§25.1）；
- 资源详情 download_count 只取 consumed 状态的 download_grants，favorite_count 取收藏行数，
  rating_summary 与 interaction 的聚合结果一致（复用而非重复实现）；
- 计数随数据变化。
本文件只验证本地可执行的业务逻辑，生产联调（PostgreSQL/Redis/MinIO）不在本机范围内。
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.modules.access.models import AccessPolicy, DownloadGrant
from app.modules.catalog import service as catalog_service
from app.modules.content import service as content_service
from app.modules.content.models import Resource, ResourceFile, ResourceVersion
from app.modules.interaction.models import Favorite, Rating
from app.modules.interaction import service as interaction_service
from app.modules.identity.models import User

import test_content_workflow as workflow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Beta14CourseCountTests(unittest.IsolatedAsyncioTestCase):
    """课程页分类计数：一次 GROUP BY，且必须过可见性谓词。"""

    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _make_resource(
        self, *, owner, resource_type, published=True, scope="public"
    ) -> Resource:
        resource = Resource(
            course_id=self.course.id,
            owner_id=owner.id,
            resource_type=resource_type,
        )
        self.db.add(resource)
        await self.db.flush()
        version = ResourceVersion(
            resource_id=resource.id,
            created_by=owner.id,
            version_no=1,
            title=f"beta14-{resource_type}",
            review_status="approved" if published else "draft",
        )
        self.db.add(version)
        await self.db.flush()
        if published:
            resource.published_version_id = version.id
            resource.publication_status = "published"
        # 每份资源都显式建访问策略：无策略行对匿名同样可见，测试要区分 public/private。
        self.db.add(AccessPolicy(resource_id=resource.id, metadata_scope=scope))
        await self.db.commit()
        return resource

    async def test_counts_exclude_private_and_unpublished_for_anonymous(self):
        # 2 篇公开笔记 + 1 份他人私有的已发布试卷；另建 1 份未发布的公开攻略。
        await self._make_resource(owner=self.owner, resource_type="note")
        await self._make_resource(owner=self.other, resource_type="note")
        await self._make_resource(owner=self.other, resource_type="exam")
        await self._make_resource(
            owner=self.other, resource_type="exam", scope="private"
        )
        await self._make_resource(
            owner=self.owner, resource_type="guide", published=False
        )

        detail = await catalog_service.get_course_detail(self.db, self.course.id)
        # 私有与未发布资源既不计入，也不泄露存在性。
        self.assertEqual(detail.resource_counts, {"note": 2, "exam": 1})

    async def test_owner_sees_own_private_resources_in_counts(self):
        await self._make_resource(owner=self.owner, resource_type="note")
        await self._make_resource(owner=self.other, resource_type="exam")
        await self._make_resource(
            owner=self.other, resource_type="exam", scope="private"
        )

        detail = await catalog_service.get_course_detail(
            self.db, self.course.id, user=self.other
        )
        # 私有资源的所有者可见，计数随之增加。
        self.assertEqual(detail.resource_counts, {"note": 1, "exam": 2})

    async def test_counts_follow_visibility_predicate_not_bare_count(self):
        """计数必须与 list_resources 的可见范围一致：匿名拿不到私有资源，计数也不能拿到。"""
        await self._make_resource(owner=self.other, resource_type="note", scope="private")

        detail = await catalog_service.get_course_detail(self.db, self.course.id)
        self.assertEqual(detail.resource_counts, {})
        from app.modules.content.service import list_resources

        page = await list_resources(
            self.db, user=None, course_id=self.course.id
        )
        self.assertEqual(len(page.items), 0)


class FavoritePlaceholder:  # pragma: no cover - 占位避免误导入，真实模型见下方
    """无实际用途：保留导入块整洁。"""


class Beta14ResourceCountTests(unittest.IsolatedAsyncioTestCase):
    """资源详情页计数：下载（consumed）/收藏/评分。"""

    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 资源详情计数要求资源已发布且公开可见（与 beta10 契约一致）。
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.third = User(
            email="third@example.test", password_hash="unused", role="user", status="active"
        )
        self.db.add(self.third)
        await self.db.flush()
        self.file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key="test-only",
            original_name="test.pdf",
        )
        self.db.add(self.file)
        await self.db.flush()
        await self.db.commit()
        # 提交会过期带 onupdate 的 updated_at，读取前先刷新（与既有契约测试同模式）。
        await self.db.refresh(self.resource)
        await self.db.refresh(self.version)

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    def _grant(self, *, user, status="consumed"):
        return DownloadGrant(
            user_id=user.id,
            resource_id=self.resource.id,
            version_id=self.version.id,
            file_id=self.file.id,
            idempotency_key=f"beta14-{uuid.uuid4()}",
            request_hash="0" * 64,
            status=status,
            valid_until=_now() + timedelta(hours=1),
        )

    async def test_detail_counts_only_consumed_grants_favorites_and_ratings(self):
        self.db.add_all(
            [
                self._grant(user=self.other, status="consumed"),
                self._grant(user=self.third, status="consumed"),
                # 未消费/已撤销/已过期的授权不计入下载数。
                self._grant(user=self.third, status="issued"),
                self._grant(user=self.other, status="revoked"),
                self._grant(user=self.other, status="expired"),
            ]
        )
        self.db.add_all(
            [
                Favorite(user_id=self.other.id, resource_id=self.resource.id),
                Favorite(user_id=self.third.id, resource_id=self.resource.id),
            ]
        )
        self.db.add_all(
            [
                Rating(
                    user_id=self.other.id,
                    resource_id=self.resource.id,
                    version_id=self.version.id,
                    score=4,
                ),
                Rating(
                    user_id=self.third.id,
                    resource_id=self.resource.id,
                    version_id=self.version.id,
                    score=5,
                ),
            ]
        )
        await self.db.commit()

        detail = await content_service.get_resource_detail(
            self.db, user=None, resource_id=self.resource.id
        )
        self.assertEqual(detail.download_count, 2)
        self.assertEqual(detail.favorite_count, 2)
        self.assertEqual(detail.rating_summary.count, 2)
        self.assertEqual(detail.rating_summary.average, 4.5)
        # 与 interaction 既有聚合完全一致（复用而非重复实现）。
        expected = await interaction_service.resource_rating_summary(
            self.db, self.resource.id
        )
        self.assertEqual(detail.rating_summary, expected)

    async def test_counts_change_with_data(self):
        detail = await content_service.get_resource_detail(
            self.db, user=None, resource_id=self.resource.id
        )
        self.assertEqual(detail.download_count, 0)
        self.assertEqual(detail.favorite_count, 0)
        self.assertEqual(detail.rating_summary.count, 0)

        self.db.add(self._grant(user=self.other, status="consumed"))
        self.db.add(Favorite(user_id=self.other.id, resource_id=self.resource.id))
        self.db.add(
            Rating(
                user_id=self.other.id,
                resource_id=self.resource.id,
                version_id=self.version.id,
                score=3,
            )
        )
        await self.db.commit()

        updated = await content_service.get_resource_detail(
            self.db, user=None, resource_id=self.resource.id
        )
        self.assertEqual(updated.download_count, 1)
        self.assertEqual(updated.favorite_count, 1)
        self.assertEqual(updated.rating_summary.count, 1)
        self.assertEqual(updated.rating_summary.average, 3.0)


if __name__ == "__main__":
    unittest.main()
