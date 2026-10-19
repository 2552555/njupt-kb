"""Beta14 排序与推荐信号（隔离 SQLite；不宣称生产并发结论）。

覆盖 beta14「排序与推荐信号」功能块：
- 资料需求榜 GET /requests?sort=hot：近 7 天开放需求按 follower_count 降序，游标独立编码；
- 资源列表 GET /resources?sort=hot：互动量按时间衰减计分，游标按 score + id；
- 规则推荐的专业信号：major 命中培养方案课程的资源排最前并带「与你专业相关」理由，
  无 profile / 无 major / 方案无覆盖时回退既有排序。

本文件只验证本地可执行的业务逻辑（service 层），生产联调（PostgreSQL）不在本机范围内。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.errors import InvalidInput
from app.core.pagination import decode_cursor, encode_cursor
from app.modules.access.models import AccessPolicy, DownloadGrant
from app.modules.catalog.models import (
    College,
    Course,
    Curriculum,
    CurriculumCourse,
    Major,
    University,
)
from app.modules.content import service as content_service
from app.modules.content.models import Resource, ResourceVersion
from app.modules.identity.models import User, UserProfile
from app.modules.interaction import service as interaction_service
from app.modules.interaction.models import Favorite, ResourceRequest, ResourceRequestFollow
from app.modules.interaction.recommendations import recommend_resources

import test_content_workflow as workflow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Beta14Base(unittest.IsolatedAsyncioTestCase):
    """公共装配：已发布且公开可见的资源 + 课程目录 + 需求数据。"""

    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _publish_resource(self, *, created_at, updated_at=None):
        """新建一门已发布、公开可见的资源；created_at 显式给定，保证衰减可控。"""
        resource = Resource(
            course_id=self.course.id,
            owner_id=self.owner.id,
            resource_type="note",
            publication_status="published",
            freshness_status="current",
            created_at=created_at,
        )
        self.db.add(resource)
        await self.db.flush()
        version = ResourceVersion(
            resource_id=resource.id,
            created_by=self.owner.id,
            version_no=1,
            title=f"resource-{resource.id}",
            review_status="approved",
        )
        self.db.add(version)
        await self.db.flush()
        resource.published_version_id = version.id
        resource.updated_at = updated_at or created_at
        await self.db.flush()
        return resource

    async def _make_request(self, *, description, created_at, followers=1):
        """创建开放需求并安排 followers 个关注（含发起者自关注）。"""
        request = ResourceRequest(
            requester_id=self.owner.id,
            course_id=self.course.id,
            description=description,
            status="open",
            dedupe_key=description,
            created_at=created_at,
        )
        self.db.add(request)
        await self.db.flush()
        self.db.add(ResourceRequestFollow(request_id=request.id, user_id=self.owner.id))
        for index in range(followers - 1):
            follower = User(
                email=f"follower-{request.id}-{index}@example.test",
                password_hash="unused",
                role="user",
                status="active",
            )
            self.db.add(follower)
            await self.db.flush()
            self.db.add(
                ResourceRequestFollow(request_id=request.id, user_id=follower.id)
            )
        await self.db.flush()
        return request


class Beta14RequestHotSortTests(_Beta14Base):
    """资料需求榜：近 7 天开放需求按 follower_count 降序，独立游标翻页。"""

    async def test_hot_sort_orders_by_follower_count_desc(self):
        now = _now()
        low = await self._make_request(
            description="低关注", created_at=now - timedelta(hours=1), followers=1
        )
        high = await self._make_request(
            description="高关注", created_at=now - timedelta(hours=2), followers=3
        )
        middle = await self._make_request(
            description="中关注", created_at=now - timedelta(hours=3), followers=2
        )
        page = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=10, sort="hot"
        )
        self.assertEqual(
            [item.id for item in page.items], [high.id, middle.id, low.id]
        )

    async def test_hot_sort_excludes_old_and_non_open_requests(self):
        now = _now()
        await self._make_request(
            description="超出 7 天窗口", created_at=now - timedelta(days=8), followers=9
        )
        closed = await self._make_request(
            description="已关闭", created_at=now - timedelta(hours=1), followers=9
        )
        closed.status = "closed"
        await self.db.flush()
        kept = await self._make_request(
            description="窗口内开放", created_at=now - timedelta(hours=1), followers=1
        )
        page = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=10, sort="hot"
        )
        self.assertEqual([item.id for item in page.items], [kept.id])

    async def test_hot_sort_cursor_paginates_without_gaps_or_overlaps(self):
        now = _now()
        made = [
            await self._make_request(
                description=f"需求-{index}",
                created_at=now - timedelta(hours=index + 1),
                # 故意构造并列关注数，验证 id  Tiebreak 与翻页稳定性。
                followers=2 if index % 2 == 0 else 3,
            )
            for index in range(5)
        ]
        first = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=2, sort="hot"
        )
        self.assertTrue(first.has_more)
        second = await interaction_service.list_requests(
            self.db,
            course_id=None,
            status=None,
            cursor=first.next_cursor,
            limit=2,
            sort="hot",
        )
        first_ids = [item.id for item in first.items]
        second_ids = [item.id for item in second.items]
        self.assertEqual(len(first_ids), 2)
        # 翻页不重复、不漏（两页并集等于全量排序的前 4 条）。
        self.assertEqual(len(set(first_ids) & set(second_ids)), 0)
        full = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=10, sort="hot"
        )
        self.assertEqual(first_ids + second_ids, [item.id for item in full.items][:4])
        # 关注数 3 的两个需求稳居第一页；关注数 2 的组（含并列）出第二页。
        self.assertEqual(set(first_ids), {made[1].id, made[3].id})
        self.assertTrue(
            set(second_ids) <= {made[0].id, made[2].id, made[4].id}
        )

    async def test_hot_and_latest_cursors_are_not_interchangeable(self):
        now = _now()
        for index in range(3):
            await self._make_request(
                description=f"混用-{index}",
                created_at=now - timedelta(hours=index + 1),
                followers=index + 1,
            )
        latest = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=1
        )
        hot = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=1, sort="hot"
        )
        # latest 游标（t/i 键）用于 hot 排序：缺 f 键，必须报错而不是静默串页。
        with self.assertRaises(InvalidInput):
            await interaction_service.list_requests(
                self.db,
                course_id=None,
                status=None,
                cursor=latest.next_cursor,
                limit=1,
                sort="hot",
            )
        # hot 游标（f/i 键）用于 latest 排序：缺 t 键，同样报错。
        with self.assertRaises(InvalidInput):
            await interaction_service.list_requests(
                self.db, course_id=None, status=None, cursor=hot.next_cursor, limit=1
            )

    async def test_latest_sort_default_behavior_unchanged(self):
        now = _now()
        older = await self._make_request(
            description="较早", created_at=now - timedelta(hours=5), followers=1
        )
        newer = await self._make_request(
            description="较新", created_at=now - timedelta(hours=1), followers=1
        )
        page = await interaction_service.list_requests(
            self.db, course_id=None, status=None, cursor=None, limit=10
        )
        self.assertEqual([item.id for item in page.items], [newer.id, older.id])
        with self.assertRaises(InvalidInput):
            await interaction_service.list_requests(
                self.db, course_id=None, status=None, cursor=None, limit=10, sort="bogus"
            )


class Beta14ResourceHotSortTests(_Beta14Base):
    """资源列表热门排序：分数 = 互动量 / (age_days + D)^P，独立游标翻页。"""

    async def test_hot_sort_orders_by_decayed_score(self):
        now = _now()
        # 同等互动量下，越新的资源分数越高（时间衰减）；基座资源零互动垫底。
        fresh = await self._publish_resource(created_at=now - timedelta(days=1))
        stale = await self._publish_resource(created_at=now - timedelta(days=30))
        for resource in (fresh, stale):
            self.db.add(Favorite(user_id=self.other.id, resource_id=resource.id))
        await self.db.flush()
        page = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=10
        )
        self.assertEqual(
            [item.id for item in page.items], [fresh.id, stale.id, self.resource.id]
        )
        # 默认 latest 排序不变：按 updated_at 倒序（基座资源最新）。
        latest = await content_service.list_resources(self.db, user=None, limit=10)
        self.assertEqual(
            [item.id for item in latest.items],
            [self.resource.id, fresh.id, stale.id],
        )

    async def _consumed_grant(self, *, resource, user, tag, status="consumed"):
        """构造一条 download_grants 记录（含文件与版本关联），status 可指定。"""
        from app.modules.content.models import ResourceFile, VersionFile

        file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key=f"uploads/{tag}.pdf",
            original_name=f"{tag}.pdf",
        )
        self.db.add(file)
        await self.db.flush()
        self.db.add(
            VersionFile(
                version_id=resource.published_version_id,
                file_id=file.id,
                purpose="primary",
                display_order=0,
            )
        )
        await self.db.flush()
        self.db.add(
            DownloadGrant(
                user_id=user.id,
                resource_id=resource.id,
                version_id=resource.published_version_id,
                file_id=file.id,
                idempotency_key=f"beta14-grant-{tag}",
                request_hash="0" * 64,
                status=status,
                valid_until=_now() + timedelta(hours=1),
            )
        )
        await self.db.flush()

    async def test_hot_sort_counts_favorites_and_consumed_grants(self):
        now = _now()
        interactive = await self._publish_resource(created_at=now - timedelta(days=2))
        plain = await self._publish_resource(created_at=now - timedelta(days=2))
        # 互动量 = 1 收藏 + 1 已消费下载；仅发放未消耗的授权不计入。
        self.db.add(Favorite(user_id=self.other.id, resource_id=interactive.id))
        await self._consumed_grant(
            resource=interactive, user=self.other, tag="consumed", status="consumed"
        )
        await self._consumed_grant(
            resource=interactive, user=self.owner, tag="issued", status="issued"
        )
        page = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=10
        )
        self.assertEqual(page.items[0].id, interactive.id)
        self.assertIn(plain.id, [item.id for item in page.items])

    async def test_hot_sort_engagement_outweighs_age_within_window(self):
        now = _now()
        # 老资源互动量足够高时仍可超过零互动的新资源（衰减而非硬截断）。
        popular_old = await self._publish_resource(created_at=now - timedelta(days=6))
        quiet_new = await self._publish_resource(created_at=now - timedelta(days=1))
        for index in range(20):
            user = User(
                email=f"fan-{index}@example.test",
                password_hash="unused",
                role="user",
                status="active",
            )
            self.db.add(user)
            await self.db.flush()
            self.db.add(Favorite(user_id=user.id, resource_id=popular_old.id))
        await self.db.flush()
        page = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=10
        )
        self.assertEqual(page.items[0].id, popular_old.id)
        self.assertIn(quiet_new.id, [item.id for item in page.items])

    async def test_hot_sort_cursor_paginates_without_overlap(self):
        now = _now()
        made = []
        for index in range(5):
            resource = await self._publish_resource(
                created_at=now - timedelta(days=index + 1)
            )
            made.append(resource)
            self.db.add(Favorite(user_id=self.other.id, resource_id=resource.id))
        await self.db.flush()
        first = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=2
        )
        self.assertTrue(first.has_more)
        # 游标带 s（score）键。
        self.assertIn("s", decode_cursor(first.next_cursor))
        second = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=2, cursor=first.next_cursor
        )
        first_ids = [item.id for item in first.items]
        second_ids = [item.id for item in second.items]
        self.assertEqual(len(set(first_ids) & set(second_ids)), 0)
        full = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=10
        )
        # 两页并集等于全量排序前 4 条，且全部来自新建资源（零互动基座垫底）。
        self.assertEqual(first_ids + second_ids, [item.id for item in full.items][:4])
        self.assertTrue(set(first_ids + second_ids) <= {item.id for item in made})

    async def test_hot_cursor_rejects_latest_cursor_and_vice_versa(self):
        now = _now()
        await self._publish_resource(created_at=now - timedelta(days=1))
        hot = await content_service.list_resources(
            self.db, user=None, sort="hot", limit=1
        )
        latest = await content_service.list_resources(self.db, user=None, limit=1)
        # 时间游标缺 s 键，用于 hot 必须报错。
        with self.assertRaises(InvalidInput):
            await content_service.list_resources(
                self.db, user=None, sort="hot", limit=1, cursor=latest.next_cursor
            )
        # 热门游标缺 ts 键，用于 latest/oldest 同样报错。
        with self.assertRaises(InvalidInput):
            await content_service.list_resources(
                self.db, user=None, limit=1, cursor=hot.next_cursor
            )

    async def test_hot_sort_rejects_unknown_sort(self):
        with self.assertRaises(InvalidInput):
            await content_service.list_resources(self.db, user=None, sort="popular")

    async def test_hot_pagination_keeps_first_page_clock_across_three_pages(self):
        now = _now()
        made = []
        for _ in range(6):
            resource = await self._publish_resource(created_at=now - timedelta(days=1))
            made.append(resource)
            self.db.add(Favorite(user_id=self.other.id, resource_id=resource.id))
        await self.db.flush()
        cursor = None
        seen = []
        for hours in (0, 4, 8):
            with patch.object(content_service, "_now", return_value=now + timedelta(hours=hours)):
                page = await content_service.list_resources(
                    self.db, user=None, sort="hot", limit=2, cursor=cursor
                )
            seen.extend(item.id for item in page.items)
            cursor = page.next_cursor
        self.assertEqual(len(set(seen)), 6)
        self.assertEqual(set(seen), {resource.id for resource in made})

    async def test_hot_cursor_rejects_nonfinite_score(self):
        for score in (float("nan"), float("inf"), -1):
            cursor = encode_cursor({"s": score, "id": str(self.resource.id), "at": _now().isoformat()})
            with self.assertRaises(InvalidInput):
                await content_service.list_resources(self.db, user=None, sort="hot", cursor=cursor)


class Beta14RecommendationMajorSignalTests(_Beta14Base):
    """规则推荐：专业信号为一级排序键，无信号时回退既有逻辑。"""

    async def _catalog(self):
        self.university = University(name="NJUPT", slug="njupt-test")
        self.db.add(self.university)
        await self.db.flush()
        self.college = College(
            university_id=self.university.id, name="计算机学院"
        )
        self.other_college = College(
            university_id=self.university.id, name="外国语学院"
        )
        self.db.add_all([self.college, self.other_college])
        await self.db.flush()
        self.major = Major(
            university_id=self.university.id,
            college_id=self.college.id,
            name="计算机科学与技术",
        )
        self.other_major = Major(
            university_id=self.university.id,
            college_id=self.other_college.id,
            name="英语",
        )
        self.db.add_all([self.major, self.other_major])
        await self.db.flush()
        self.curriculum = Curriculum(
            major_id=self.major.id, entry_year=2024, name="2024 级培养方案"
        )
        self.db.add(self.curriculum)
        await self.db.flush()
        # 专业课程与一门无关课程。
        self.major_course = Course(
            university_id=self.university.id, name="数据结构", code="CS201"
        )
        self.unrelated_course = Course(
            university_id=self.university.id, name="综合英语", code="EN101"
        )
        self.db.add_all([self.major_course, self.unrelated_course])
        await self.db.flush()
        self.db.add(
            CurriculumCourse(
                curriculum_id=self.curriculum.id, course_id=self.major_course.id
            )
        )
        await self.db.flush()

    async def test_major_match_resource_ranks_first_with_reason(self):
        await self._catalog()
        now = _now()
        major_resource = await self._publish_resource_at(
            self.major_course.id, created_at=now - timedelta(days=1)
        )
        await self._publish_resource_at(
            self.unrelated_course.id, created_at=now - timedelta(days=1)
        )
        profile = UserProfile(
            user_id=self.other.id,
            display_name="同学",
            major_id=self.major.id,
            college_id=self.college.id,
            enrollment_year=2024,
        )
        self.db.add(profile)
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=4)
        top = result.items[0]
        self.assertEqual(top.resource_id, major_resource.id)
        self.assertIn("与你专业相关", top.reasons)

    async def test_no_profile_falls_back_to_existing_ordering(self):
        await self._catalog()
        now = _now()
        major_resource = await self._publish_resource_at(
            self.major_course.id, created_at=now - timedelta(days=2)
        )
        unrelated = await self._publish_resource_at(
            self.unrelated_course.id, created_at=now - timedelta(days=1)
        )
        result = await recommend_resources(self.db, user=self.other, limit=4)
        # 无 profile：不加专业列，回退 updated_at 倒序（基座资源最新）。
        self.assertEqual(
            [item.resource_id for item in result.items],
            [self.resource.id, unrelated.id, major_resource.id],
        )
        for item in result.items:
            self.assertNotIn("与你专业相关", item.reasons)

    async def test_missing_major_does_not_error(self):
        await self._catalog()
        now = _now()
        await self._publish_resource_at(
            self.major_course.id, created_at=now - timedelta(days=1)
        )
        # 有 profile 但未填专业/学院：不得报错，排序回退。
        profile = UserProfile(
            user_id=self.other.id, display_name="同学", major_id=None, college_id=None
        )
        self.db.add(profile)
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=4)
        self.assertTrue(result.items)

    async def test_major_only_profile_recommends_without_college(self):
        await self._catalog()
        resource = await self._publish_resource_at(self.major_course.id, created_at=_now())
        self.db.add(UserProfile(user_id=self.other.id, display_name="同学", major_id=self.major.id))
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=4)
        self.assertEqual(result.items[0].resource_id, resource.id)
        self.assertIn("与你专业相关", result.items[0].reasons)

    async def test_college_only_profile_recommends_without_major(self):
        await self._catalog()
        resource = await self._publish_resource_at(self.major_course.id, created_at=_now())
        self.db.add(UserProfile(user_id=self.other.id, display_name="同学", college_id=self.college.id))
        await self.db.flush()
        result = await recommend_resources(self.db, user=self.other, limit=4)
        self.assertEqual(result.items[0].resource_id, resource.id)
        self.assertIn("与你学院相关", result.items[0].reasons)

    async def test_curriculum_without_coverage_falls_back(self):
        await self._catalog()
        now = _now()
        first = await self._publish_resource_at(
            self.major_course.id, created_at=now - timedelta(days=1)
        )
        profile = UserProfile(
            user_id=self.other.id,
            display_name="同学",
            major_id=self.major.id,
            college_id=self.college.id,
            enrollment_year=2024,
        )
        self.db.add(profile)
        await self.db.flush()
        # 培养方案未关联任何课程：清空 curriculum_courses 后不应报错、不加专业理由。
        from sqlalchemy import delete

        from app.modules.catalog.models import CurriculumCourse

        await self.db.execute(delete(CurriculumCourse))
        result = await recommend_resources(self.db, user=self.other, limit=4)
        self.assertTrue(result.items)
        for item in result.items:
            self.assertNotIn("与你专业相关", item.reasons)
        self.assertIn(first.id, [item.resource_id for item in result.items])

    async def _publish_resource_at(self, course_id, *, created_at):
        """在指定课程下发布公开可见资源。"""
        resource = Resource(
            course_id=course_id,
            owner_id=self.owner.id,
            resource_type="note",
            publication_status="published",
            freshness_status="current",
            created_at=created_at,
        )
        self.db.add(resource)
        await self.db.flush()
        version = ResourceVersion(
            resource_id=resource.id,
            created_by=self.owner.id,
            version_no=1,
            title=f"resource-{resource.id}",
            review_status="approved",
        )
        self.db.add(version)
        await self.db.flush()
        resource.published_version_id = version.id
        resource.updated_at = created_at
        self.db.add(AccessPolicy(resource_id=resource.id, metadata_scope="public"))
        await self.db.flush()
        return resource


if __name__ == "__main__":
    unittest.main()
