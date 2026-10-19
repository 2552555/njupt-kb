"""beta16 统一搜索测试（隔离 SQLite；不宣称生产并发或 PostgreSQL 迁移结论）。

覆盖：统一搜索三组命中、私有/未发布过滤、归一化与日志、输入校验、scope 过滤、
建议前缀优先与上限、建议不写日志、热词排除零结果/单字符与排序、日志开关、
保留期清理、日志表不含用户标识，以及路由契约。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, inspect, select

from app.config import settings
from app.core.cache import reset as reset_cache
from app.core.errors import InvalidInput
from app.modules.catalog.models import Course
from app.modules.content.models import Resource, ResourceVersion
from app.modules.identity.models import UserProfile
from app.modules.search import service as search_service
from app.modules.search.models import SearchQuery

try:  # `python -m unittest discover -s tests -t tests`
    import test_content_workflow as workflow
except ModuleNotFoundError:  # 兼容 `python -m unittest tests.test_beta16_search`
    from tests import test_content_workflow as workflow


class Beta16SearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 热词走进程内缓存；每个用例使用独立内存库，必须清掉上一个用例的缓存。
        reset_cache()
        self.owner_profile = UserProfile(
            user_id=self.owner.id, display_name="默认贡献者"
        )
        self.other_profile = UserProfile(
            user_id=self.other.id, display_name="其他用户"
        )
        self.db.add_all([self.owner_profile, self.other_profile])
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # --- 构造数据 -----------------------------------------------------------

    async def _add_resource(
        self,
        *,
        owner=None,
        title: str,
        published: bool = False,
        metadata_scope: str | None = None,
        course: Course | None = None,
    ) -> tuple[Resource, ResourceVersion]:
        """新建资源与版本；published=True 时设置发布指针与可选访问策略。"""
        resource = Resource(
            course_id=(course or self.course).id,
            owner_id=(owner or self.owner).id,
            resource_type="note",
        )
        self.db.add(resource)
        await self.db.flush()
        version = ResourceVersion(
            resource_id=resource.id,
            created_by=(owner or self.owner).id,
            version_no=1,
            title=title,
            review_status="draft",
        )
        self.db.add(version)
        await self.db.flush()
        if published:
            version.review_status = "approved"
            resource.published_version_id = version.id
            resource.publication_status = "published"
            if metadata_scope is not None:
                from app.modules.access.models import AccessPolicy

                self.db.add(
                    AccessPolicy(
                        resource_id=resource.id, metadata_scope=metadata_scope
                    )
                )
        await self.db.commit()
        return resource, version

    # --- 统一搜索 -----------------------------------------------------------

    async def test_search_hits_courses_resources_and_creators(self):
        self.course.name = "数据结构"
        extra_course = Course(
            university_id=self.school.id, name="数据结构实验", code="DS2"
        )
        self.db.add(extra_course)
        self.owner_profile.display_name = "数据结构王老师"
        await self._add_resource(
            title="数据结构复习笔记", published=True, metadata_scope="public"
        )

        result = await search_service.search(
            self.db, user=None, q="数据结构", scope="all", limit=10
        )

        self.assertEqual(
            {hit.id for hit in result.courses},
            {self.course.id, extra_course.id},
        )
        self.assertEqual(result.courses[0].id, self.course.id)
        self.assertEqual(len(result.resources), 1)
        self.assertEqual(result.resources[0].title, "数据结构复习笔记")
        self.assertEqual([hit.id for hit in result.creators], [self.owner.id])
        self.assertEqual(result.creators[0].resource_count, 1)
        self.assertEqual(result.total, 4)
        self.assertEqual(result.query, "数据结构")

    async def test_private_and_unpublished_resources_are_excluded(self):
        self.version.title = "机密笔记"
        await workflow.ContentWorkflowTests.publish_private(self)
        await self._add_resource(title="草稿笔记")

        anonymous = await search_service.search(
            self.db, user=None, q="笔记", scope="resource"
        )
        self.assertEqual(anonymous.resources, [])

        # 作者本人可见私有已发布资源，但草稿仍不进入统一搜索。
        owner_view = await search_service.search(
            self.db, user=self.owner, q="笔记", scope="resource"
        )
        self.assertEqual(
            [hit.title for hit in owner_view.resources], ["机密笔记"]
        )

    async def test_normalization_matches_and_logs_normalized_term(self):
        await self._add_resource(
            title="Data Structure Notes", published=True, metadata_scope="public"
        )

        result = await search_service.search(
            self.db, user=None, q="  DATA   Structure  "
        )
        self.assertEqual([hit.title for hit in result.resources], ["Data Structure Notes"])

        await self.db.commit()
        log = (
            await self.db.execute(select(SearchQuery).order_by(SearchQuery.created_at))
        ).scalars().all()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].query_norm, "data structure")
        self.assertEqual(log[0].scope, "all")
        self.assertFalse(log[0].zero_result)

    async def test_empty_and_too_long_query_raise_invalid_input(self):
        for q in ("", "   ", "x" * (settings.SEARCH_QUERY_MAX_CHARS + 1)):
            with self.assertRaises(InvalidInput):
                await search_service.search(self.db, user=None, q=q)

    async def test_zero_result_search_is_logged_for_observation(self):
        result = await search_service.search(
            self.db, user=None, q="完全不存在的词", scope="all"
        )
        self.assertEqual(result.total, 0)
        await self.db.commit()

        log = (await self.db.execute(select(SearchQuery))).scalars().one()
        self.assertEqual(log.result_count, 0)
        self.assertTrue(log.zero_result)

    async def test_scope_filter_returns_only_requested_group(self):
        self.course.name = "数据结构"
        self.owner_profile.display_name = "数据结构王"
        await self._add_resource(
            title="数据结构笔记", published=True, metadata_scope="public"
        )

        course_only = await search_service.search(
            self.db, user=None, q="数据结构", scope="course"
        )
        self.assertTrue(course_only.courses)
        self.assertEqual(course_only.resources, [])
        self.assertEqual(course_only.creators, [])

        resource_only = await search_service.search(
            self.db, user=None, q="数据结构", scope="resource"
        )
        self.assertEqual(resource_only.courses, [])
        self.assertTrue(resource_only.resources)
        self.assertEqual(resource_only.creators, [])

        creator_only = await search_service.search(
            self.db, user=None, q="数据结构", scope="creator"
        )
        self.assertEqual(creator_only.courses, [])
        self.assertEqual(creator_only.resources, [])
        self.assertTrue(creator_only.creators)

    # --- 搜索建议 -----------------------------------------------------------

    async def test_suggest_prefix_first_and_respects_limit(self):
        self.course.name = "算法"
        self.db.add_all(
            [
                Course(university_id=self.school.id, name="算法导论", code="AL2"),
                Course(university_id=self.school.id, name="高级算法", code="AL3"),
            ]
        )
        await self.db.commit()

        full = await search_service.suggest(self.db, user=None, q="算法")
        self.assertEqual(
            [item.label for item in full.items],
            ["算法", "算法导论", "高级算法"],
        )

        with patch.object(settings, "SEARCH_SUGGEST_LIMIT", 2):
            limited = await search_service.suggest(self.db, user=None, q="算法")
        self.assertEqual(len(limited.items), 2)
        self.assertEqual(limited.items[0].label, "算法")

    async def test_suggest_too_short_returns_empty_without_error(self):
        with patch.object(settings, "SEARCH_SUGGEST_MIN_CHARS", 3):
            result = await search_service.suggest(self.db, user=None, q="算法")
        self.assertEqual(result.items, [])

    async def test_suggest_does_not_write_query_log(self):
        self.course.name = "算法"
        await self.db.commit()
        before = await self.db.scalar(select(func.count()).select_from(SearchQuery))

        await search_service.suggest(self.db, user=None, q="算法")

        after = await self.db.scalar(select(func.count()).select_from(SearchQuery))
        self.assertEqual(after, before)

    # --- 热词 ---------------------------------------------------------------

    async def test_hot_excludes_zero_result_and_single_char(self):
        now = datetime.now(timezone.utc)
        self.db.add_all(
            [
                SearchQuery(
                    query_norm="数据结构",
                    scope="all",
                    result_count=2,
                    zero_result=False,
                    created_at=now,
                ),
                SearchQuery(
                    query_norm="操作系统",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now,
                ),
                SearchQuery(
                    query_norm="x",
                    scope="all",
                    result_count=10,
                    zero_result=False,
                    created_at=now,
                ),
                SearchQuery(
                    query_norm="零结果词",
                    scope="all",
                    result_count=0,
                    zero_result=True,
                    created_at=now,
                ),
                SearchQuery(
                    query_norm="窗口外热词",
                    scope="all",
                    result_count=100,
                    zero_result=False,
                    created_at=now
                    - timedelta(days=settings.SEARCH_HOT_WINDOW_DAYS + 1),
                ),
            ]
        )
        await self.db.commit()

        result = await search_service.hot_queries(self.db)
        self.assertEqual(
            {item.query for item in result.items}, {"数据结构", "操作系统"}
        )
        self.assertEqual(result.window_days, settings.SEARCH_HOT_WINDOW_DAYS)

    async def test_hot_orders_by_count_then_recent(self):
        now = datetime.now(timezone.utc)
        self.db.add_all(
            [
                SearchQuery(
                    query_norm="alpha",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=3),
                ),
                SearchQuery(
                    query_norm="beta",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=2),
                ),
                SearchQuery(
                    query_norm="beta",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=2),
                ),
                SearchQuery(
                    query_norm="beta",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=2),
                ),
                SearchQuery(
                    query_norm="beta",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=2),
                ),
                SearchQuery(
                    query_norm="beta",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=2),
                ),
                SearchQuery(
                    query_norm="gamma",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=1),
                ),
                SearchQuery(
                    query_norm="gamma",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=1),
                ),
                SearchQuery(
                    query_norm="gamma",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=1),
                ),
                SearchQuery(
                    query_norm="gamma",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=1),
                ),
                SearchQuery(
                    query_norm="gamma",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(hours=1),
                ),
            ]
        )
        await self.db.commit()

        result = await search_service.hot_queries(self.db)
        self.assertEqual(
            [item.query for item in result.items], ["gamma", "beta", "alpha"]
        )

    # --- 日志开关、保留期与隐私 --------------------------------------------

    async def test_log_disabled_writes_nothing(self):
        with patch.object(settings, "SEARCH_LOG_ENABLED", False):
            await search_service.search(self.db, user=None, q="任意词")
        await self.db.commit()

        count = await self.db.scalar(select(func.count()).select_from(SearchQuery))
        self.assertEqual(count, 0)

    async def test_retention_removes_only_expired_logs(self):
        now = datetime.now(timezone.utc)
        self.db.add_all(
            [
                SearchQuery(
                    query_norm="过期词",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now
                    - timedelta(days=settings.SEARCH_LOG_RETENTION_DAYS + 1),
                ),
                SearchQuery(
                    query_norm="近期词",
                    scope="all",
                    result_count=1,
                    zero_result=False,
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        await self.db.commit()

        deleted = await search_service.run_retention_cleanup(self.db)
        await self.db.commit()

        self.assertEqual(deleted, 1)
        remaining = (
            await self.db.execute(select(SearchQuery.query_norm))
        ).scalars().all()
        self.assertEqual(remaining, ["近期词"])

    async def test_log_table_has_no_user_identifier_columns(self):
        columns = {column.key for column in inspect(SearchQuery).columns}
        self.assertTrue(
            {"query_norm", "scope", "result_count", "zero_result", "created_at"}
            <= columns
        )
        self.assertFalse(
            {"user_id", "ip", "ip_address", "ua", "user_agent"} & columns
        )

    # --- 路由契约 -----------------------------------------------------------

    async def test_router_contract_for_search_suggest_and_hot(self):
        import httpx
        from fastapi import FastAPI

        from app.core.deps import get_optional_user
        from app.core.errors import install_error_handlers
        from app.db import get_db
        from app.modules.ratelimit.deps import get_counter_db
        from app.modules.search.router import router as search_router

        self.course.name = "数据结构"
        await self._add_resource(
            title="数据结构笔记", published=True, metadata_scope="public"
        )

        app = FastAPI()
        install_error_handlers(app)
        app.include_router(search_router, prefix="/api/v1")

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_counter_db] = database
        app.dependency_overrides[get_optional_user] = lambda: None

        with patch.object(settings, "RATE_LIMIT_ENABLED", False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                search_response = await client.get(
                    "/api/v1/search", params={"q": "数据结构"}
                )
                suggest_response = await client.get(
                    "/api/v1/search/suggest", params={"q": "数据"}
                )
                hot_response = await client.get("/api/v1/search/hot")
                blank_response = await client.get(
                    "/api/v1/search", params={"q": "   "}
                )

        self.assertEqual(search_response.status_code, 200, search_response.text)
        body = search_response.json()
        self.assertEqual(
            set(body), {"query", "scope", "courses", "resources", "creators", "total"}
        )
        self.assertEqual(body["scope"], "all")
        self.assertTrue(body["resources"])
        self.assertEqual(
            set(body["resources"][0]),
            {"id", "title", "resource_type", "course_id", "course_name"},
        )

        self.assertEqual(suggest_response.status_code, 200)
        self.assertEqual(set(suggest_response.json()["items"][0]), {
            "type", "id", "label", "sublabel", "href"
        })
        self.assertEqual(hot_response.status_code, 200)
        self.assertEqual(set(hot_response.json()), {"items", "window_days"})
        self.assertEqual(blank_response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
