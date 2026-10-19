"""统一搜索服务（设计文档 §22）。

业务规则的唯一落点；router 只解析参数（docs/ARCHITECTURE.md §1）。

设计取舍：
- 不引入 Elasticsearch/Meilisearch：本版只用 PostgreSQL 与 SQLite 都能编译的
  `ILIKE` / `LIKE`、`CASE` 与 `GROUP BY`，保证本地测试库与生产库行为一致。
- 排序先相关性（完全匹配 > 前缀 > 包含），同档按热度或创建时间兜底；不做跨分组
  混排，前端按课程/资源/创作者分区展示。
- 课程复用 catalog 的公开条件语义；资源必须同时满足「已发布 + 有发布版本 +
  当前查看者可见」，可见性谓词在 LIMIT 之前生效，避免私有资源经分页泄露存在性。
- 查询日志只写归一化词与结果计数；写日志失败只记应用日志，绝不影响搜索响应。
- 建议是高频输入，不写日志；热词读取可走进程内 `PUBLIC_READ` 缓存，缓存故障时
  回退为空列表或直连数据库，不把缓存故障放大成搜索失败。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.cache import get_or_set
from app.core.enums import AccountStatus, CacheClass, PublicationStatus
from app.core.errors import InvalidInput
from app.modules.search.models import SearchQuery
from app.modules.search.schemas import (
    CourseHit,
    CreatorHit,
    HotItem,
    HotOut,
    ResourceHit,
    SearchOut,
    SearchScope,
    SuggestItem,
    SuggestOut,
    SuggestType,
)

logger = logging.getLogger("app.search")

_WHITESPACE = re.compile(r"\s+")
_LIKE_ESCAPE = "\\"

# 建议项同档时的展示顺序：课程 → 资源 → 创作者，避免同分时顺序随机。
_TYPE_ORDER = {
    SuggestType.COURSE: 0,
    SuggestType.RESOURCE: 1,
    SuggestType.CREATOR: 2,
}

# 单次统一搜索每个分组的条数上限；router 也限制 limit，服务层再兜底一次。
_MAX_GROUP_LIMIT = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_query(raw: str) -> str:
    """归一化查询词：去首尾空白、压缩连续空白、转小写并按配置截断（§22）。"""
    collapsed = _WHITESPACE.sub(" ", (raw or "").strip())
    return collapsed.lower()[: settings.SEARCH_QUERY_MAX_CHARS]


def _require_query(raw: str | None) -> str:
    """搜索入口的必填校验；归一化后为空同样视为无效输入。"""
    text = (raw or "").strip()
    if not text:
        raise InvalidInput("搜索词不能为空")
    if len(text) > settings.SEARCH_QUERY_MAX_CHARS:
        raise InvalidInput(
            "搜索词过长",
            details={"max_chars": settings.SEARCH_QUERY_MAX_CHARS},
        )
    normalized = normalize_query(text)
    if not normalized:
        raise InvalidInput("搜索词不能为空")
    return normalized


def _scope_value(scope: str | SearchScope) -> str:
    try:
        return SearchScope(scope).value
    except ValueError as exc:
        raise InvalidInput("scope 无效", details={"reason": "unsupported_scope"}) from exc


def _group_limit(limit: int | None) -> int:
    if limit is None:
        return 10
    if limit < 1:
        raise InvalidInput("limit 必须为正整数")
    return min(int(limit), _MAX_GROUP_LIMIT)


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，用户输入的 `%`/`_` 只按字面匹配。"""
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def _contains(value: str) -> str:
    return f"%{_escape_like(value)}%"


def _prefix(value: str) -> str:
    return f"{_escape_like(value)}%"


def _visible_resource_ids(user: Any):
    """跨模块可见性谓词：函数体内局部导入，避免模块顶层耦合（§2.2）。"""
    from app.modules.access.visibility import visible_resource_ids

    return visible_resource_ids(user)


# --------------------------------------------------------------------------- #
# 统一搜索
# --------------------------------------------------------------------------- #


async def search(
    db: AsyncSession,
    *,
    user: Any,
    q: str | None,
    scope: str | SearchScope = SearchScope.ALL,
    limit: int = 10,
) -> SearchOut:
    """统一搜索入口：按 scope 召回课程/资源/创作者并记录一条聚合日志。

    `limit` 作用于每个分组；`total` 为本次实际返回条数之和，便于前端显示与
    零结果观测。日志写入失败不回滚搜索，也不改变响应。
    """
    normalized = _require_query(q)
    scope_value = _scope_value(scope)
    size = _group_limit(limit)

    courses: list[CourseHit] = []
    resources: list[ResourceHit] = []
    creators: list[CreatorHit] = []

    if scope_value in (SearchScope.ALL.value, SearchScope.COURSE.value):
        courses = [hit for _, hit in await _course_hits(db, normalized, size)]
    if scope_value in (SearchScope.ALL.value, SearchScope.RESOURCE.value):
        resources = [
            hit for _, hit in await _resource_hits(db, user, normalized, size)
        ]
    if scope_value in (SearchScope.ALL.value, SearchScope.CREATOR.value):
        creators = [hit for _, hit in await _creator_hits(db, user, normalized, size)]

    total = len(courses) + len(resources) + len(creators)
    await _record_query(db, normalized, scope_value, total)

    return SearchOut(
        query=normalized,
        scope=SearchScope(scope_value),
        courses=courses,
        resources=resources,
        creators=creators,
        total=total,
    )


async def _course_hits(
    db: AsyncSession, query: str, limit: int
) -> list[tuple[int, CourseHit]]:
    """公开课程召回：名称/代码/别名命中，精确 > 前缀 > 包含（§11.2、§19.1）。"""
    from app.modules.catalog.models import College, Course, CourseAlias
    from app.modules.catalog.service import _public_course_condition

    contains = _contains(query)
    prefix = _prefix(query)
    alias_sub = select(CourseAlias.course_id).where(
        CourseAlias.alias.ilike(contains, escape=_LIKE_ESCAPE)
    )
    rank = case(
        (func.lower(Course.name) == query, 0),
        (func.lower(Course.code) == query, 0),
        (Course.name.ilike(prefix, escape=_LIKE_ESCAPE), 1),
        (Course.code.ilike(prefix, escape=_LIKE_ESCAPE), 1),
        else_=2,
    )
    stmt = (
        select(Course.id, Course.name, Course.code, College.name, rank)
        .outerjoin(College, College.id == Course.owning_college_id)
        .where(
            _public_course_condition(),
            or_(
                Course.name.ilike(contains, escape=_LIKE_ESCAPE),
                Course.code.ilike(contains, escape=_LIKE_ESCAPE),
                Course.id.in_(alias_sub),
            ),
        )
        .order_by(rank, Course.name, Course.id)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        (
            int(row[4]),
            CourseHit(
                id=row[0], name=row[1], code=row[2], college_name=row[3]
            ),
        )
        for row in rows
    ]


async def _resource_hits(
    db: AsyncSession, user: Any, query: str, limit: int
) -> list[tuple[int, ResourceHit]]:
    """已发布资源召回：标题取当前发布版本，并在 LIMIT 前应用可见性谓词。"""
    from app.modules.catalog.models import Course
    from app.modules.content.models import Resource, ResourceVersion

    contains = _contains(query)
    prefix = _prefix(query)
    rank = case(
        (func.lower(ResourceVersion.title) == query, 0),
        (ResourceVersion.title.ilike(prefix, escape=_LIKE_ESCAPE), 1),
        else_=2,
    )
    stmt = (
        select(
            Resource.id,
            ResourceVersion.title,
            Resource.resource_type,
            Resource.course_id,
            Course.name,
            rank,
        )
        .join(ResourceVersion, ResourceVersion.id == Resource.published_version_id)
        .join(Course, Course.id == Resource.course_id)
        .where(
            Resource.publication_status == PublicationStatus.PUBLISHED.value,
            Resource.published_version_id.is_not(None),
            ResourceVersion.title.ilike(contains, escape=_LIKE_ESCAPE),
            Resource.id.in_(_visible_resource_ids(user)),
        )
        .order_by(rank, Resource.created_at.desc(), Resource.id)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        (
            int(row[5]),
            ResourceHit(
                id=row[0],
                title=row[1],
                resource_type=row[2],
                course_id=row[3],
                course_name=row[4],
            ),
        )
        for row in rows
    ]


def _creator_published_subquery(user: Any):
    """至少有一份「已发布且对查看者可见」资源的创作者及数量。

    计数也走可见性谓词：不把私有资源的存在性通过「资源数」泄露给其他查看者。
    """
    from app.modules.content.models import Resource

    return (
        select(
            Resource.owner_id.label("owner_id"),
            func.count().label("resource_count"),
        )
        .where(
            Resource.publication_status == PublicationStatus.PUBLISHED.value,
            Resource.published_version_id.is_not(None),
            Resource.id.in_(_visible_resource_ids(user)),
        )
        .group_by(Resource.owner_id)
        .subquery()
    )


async def _creator_hits(
    db: AsyncSession, user: Any, query: str, limit: int
) -> list[tuple[int, CreatorHit]]:
    """创作者召回：显示名命中，按相关性后按已发布资源数降序。"""
    from app.modules.identity.models import User, UserProfile

    contains = _contains(query)
    prefix = _prefix(query)
    published = _creator_published_subquery(user)
    display_name = func.coalesce(UserProfile.display_name, "用户")
    rank = case(
        (func.lower(display_name) == query, 0),
        (display_name.ilike(prefix, escape=_LIKE_ESCAPE), 1),
        else_=2,
    )
    stmt = (
        select(
            User.id,
            display_name.label("display_name"),
            published.c.resource_count,
            rank,
        )
        .join(published, published.c.owner_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(
            User.status == AccountStatus.ACTIVE.value,
            display_name.ilike(contains, escape=_LIKE_ESCAPE),
        )
        .order_by(
            rank,
            published.c.resource_count.desc(),
            display_name,
            User.id,
        )
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        (
            int(row[3]),
            CreatorHit(
                id=row[0], display_name=row[1], resource_count=int(row[2])
            ),
        )
        for row in rows
    ]


async def _record_query(
    db: AsyncSession, query_norm: str, scope: str, result_count: int
) -> None:
    """写一条聚合日志；只存归一化词与计数，失败只记日志、不影响响应（§22）。"""
    if not settings.SEARCH_LOG_ENABLED:
        return
    try:
        # SAVEPOINT：日志写失败只回滚这一段，不把调用方的搜索事务置于失败状态。
        async with db.begin_nested():
            db.add(
                SearchQuery(
                    query_norm=query_norm,
                    scope=scope,
                    result_count=result_count,
                    zero_result=result_count == 0,
                )
            )
            await db.flush()
    except Exception:  # noqa: BLE001 - 观测日志绝不能影响搜索主路径
        logger.warning("search_query_log_failed", exc_info=True)


# --------------------------------------------------------------------------- #
# 搜索建议
# --------------------------------------------------------------------------- #


async def suggest(db: AsyncSession, *, user: Any, q: str | None) -> SuggestOut:
    """输入联想：前缀优先、其次包含；不足最小长度返回空列表而不是报错。

    建议接口不写查询日志：它随每次按键触发，写日志会把观测数据放大成写入压力。
    """
    normalized = normalize_query(q or "")
    if len(normalized) < settings.SEARCH_SUGGEST_MIN_CHARS:
        return SuggestOut(items=[])

    limit = settings.SEARCH_SUGGEST_LIMIT
    entries: list[tuple[int, SuggestItem]] = []

    for rank, hit in await _course_hits(db, normalized, limit):
        entries.append(
            (
                rank,
                SuggestItem(
                    type=SuggestType.COURSE,
                    id=hit.id,
                    label=hit.name,
                    sublabel=hit.code or hit.college_name,
                    href=f"/courses/{hit.id}",
                ),
            )
        )
    for rank, hit in await _resource_hits(db, user, normalized, limit):
        entries.append(
            (
                rank,
                SuggestItem(
                    type=SuggestType.RESOURCE,
                    id=hit.id,
                    label=hit.title,
                    sublabel=hit.course_name,
                    href=f"/resources/{hit.id}",
                ),
            )
        )
    for rank, hit in await _creator_hits(db, user, normalized, limit):
        entries.append(
            (
                rank,
                SuggestItem(
                    type=SuggestType.CREATOR,
                    id=hit.id,
                    label=hit.display_name,
                    sublabel=f"{hit.resource_count} 份资料",
                    href=f"/creators/{hit.id}",
                ),
            )
        )

    entries.sort(key=lambda entry: (entry[0], _TYPE_ORDER[entry[1].type]))
    return SuggestOut(items=[item for _, item in entries[:limit]])


# --------------------------------------------------------------------------- #
# 热词
# --------------------------------------------------------------------------- #


async def hot_queries(db: AsyncSession) -> HotOut:
    """窗口内热词：排除零结果与单字符，按次数降序、同次数按最近出现降序。"""
    window_days = settings.SEARCH_HOT_WINDOW_DAYS
    limit = settings.SEARCH_HOT_LIMIT
    cache_key = f"search:hot:{window_days}:{limit}"

    async def load() -> list[HotItem]:
        return await _load_hot_queries(db, window_days, limit)

    # PUBLIC_READ：非敏感公共聚合；缓存或回源失败时返回空列表，不影响页面可用性。
    items = await get_or_set(
        cache_key,
        load,
        cache_class=CacheClass.PUBLIC_READ,
        ttl=settings.CACHE_PUBLIC_TTL_SECONDS,
    )
    return HotOut(items=list(items or []), window_days=window_days)


async def _load_hot_queries(
    db: AsyncSession, window_days: int, limit: int
) -> list[HotItem]:
    cutoff = _now() - timedelta(days=window_days)
    count_expr = func.count()
    last_seen = func.max(SearchQuery.created_at)
    stmt: Select = (
        select(SearchQuery.query_norm, count_expr, last_seen)
        .where(
            SearchQuery.created_at >= cutoff,
            SearchQuery.zero_result.is_(False),
            func.length(SearchQuery.query_norm) >= 2,
        )
        .group_by(SearchQuery.query_norm)
        .order_by(count_expr.desc(), last_seen.desc(), SearchQuery.query_norm)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [HotItem(query=row[0], count=int(row[1])) for row in rows]


# --------------------------------------------------------------------------- #
# 保留期清理
# --------------------------------------------------------------------------- #


async def run_retention_cleanup(db: AsyncSession) -> int:
    """删除超过保留期的查询日志，返回删除行数；只动 `search_queries`。"""
    cutoff = _now() - timedelta(days=settings.SEARCH_LOG_RETENTION_DAYS)
    result = await db.execute(
        delete(SearchQuery)
        .where(SearchQuery.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
