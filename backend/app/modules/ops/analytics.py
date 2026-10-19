"""管理端数据分析总览（设计文档 §43「数据分析」、§45「可观测性」）。

只读聚合：一次请求按 UTC 自然日分桶返回用户/内容/搜索之外的互动与治理指标，
不写库、不加缓存、不建定时任务。每个指标一条 ``GROUP BY date`` 聚合，
查询数与窗口长度无关（固定 11 条指标 + 3 条漏斗 + 1 条课程 Top），
不在 Python 里逐天回查（§29 避免 N+1）。

与 §45 的分工：本端点只放产品/运营指标；连接池、缓存、事件积压等技术指标
仍由 ``/admin/metrics`` 提供，两类指标不混在一张表里。

指标口径（均按 UTC 自然日，窗口左闭右开 ``[start_day 00:00, now)``）：

- ``new_users``：该日新建且当前未注销（``users.deleted_at IS NULL``）的账号。
  注销账号仍保留历史行，故不回溯改写过去日期的计数。
- ``new_resources``：该日新建的资源行，任何发布状态都计入。
- ``published_versions``：该日进入「已发布」状态的版本。内容表没有发布时间列
  （``ResourceVersion`` 只有 ``submitted_at``，``Resource`` 只有 ``updated_at``，
  后者会被任意编辑刷新），故以发布事务内写入的审计 ``resource.published`` 为
  事实来源，一次发布/回滚各计一次。
- ``downloads``：该日签发的下载授权数（``download_grants.created_at``），
  含后续被撤销的授权：签发是不可变事实。
- ``favorites``：该日新增收藏。
- ``ratings``：该日新增或更新评分（``ratings.updated_at``）。同一用户对同一版本
  只有一条有效评分，改分只体现为一次「更新」。
- ``comments``：该日新增评论，含已隐藏/已撤回的评论（内容治理不改变「发生过」）。
- ``reports``：该日新增举报工单。
- ``corrections``：该日新增纠错工单。
- ``requests``：该日新增求资料需求。
- ``cases``：该日新建案件。

漏斗口径（窗口内绝对量，不伪造「用户转化率」）：

- ``uploaded``：新建上传会话数；
- ``submitted``：版本提交时间落在窗口内的版本数；
- ``approved``：窗口内作出「通过」决定且仍为 approved 的审核任务数；
- ``published``：窗口内发布过的资源数（审计 ``resource.published`` 去重对象）；
- ``downloaded``：窗口内签发的下载授权数（与 ``downloads`` 总计一致）。

商业与 AI 指标不纳入：``COMMERCE_ENABLED`` 与 AI 开关默认关闭，统计它们会
制造「全 0 即业务为零」的误读（§43 的前提是功能已上线）。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import InvalidInput
from app.modules.ops.schemas import (
    AnalyticsCounts,
    AnalyticsFunnelOut,
    AnalyticsOverviewOut,
    AnalyticsPoint,
    TopCourseItem,
)

# 对外指标名顺序：与 `AnalyticsCounts` 字段一致，序列化与求和都按此顺序。
METRIC_KEYS: tuple[str, ...] = (
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

TOP_COURSES_LIMIT = 10

DATA_NOTES: tuple[str, ...] = (
    "统计为只读聚合，非实时报表：结果取自查询时刻的数据库快照，不写库、不建缓存。",
    "商业与 AI 指标未纳入：COMMERCE_ENABLED 与 AI 相关开关默认关闭，避免把「未启用」误读为「零使用」。",
    "时间按 UTC 自然日分桶，窗口内没有数据的日期补 0，前端可直接连续画图。",
    "「已发布版本」以审计 resource.published 为事实来源（内容表无发布时间列），一次发布或回滚各计一次。",
)


def _utc_day(db: AsyncSession, column):
    """按 UTC 自然日截断时间列，兼容 PostgreSQL 与测试用 SQLite。

    PostgreSQL 的 ``date(timestamptz)`` 依赖会话时区，故先 ``timezone('UTC', ...)``；
    SQLite 侧时间统一以 UTC 写入且不保存偏移，``date()`` 即为 UTC 日期。
    """
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    if dialect is not None and dialect.name == "sqlite":
        return func.date(column)
    return func.date(func.timezone("UTC", column))


async def _daily_counts(
    db: AsyncSession,
    column,
    start: datetime,
    end: datetime,
    *conditions,
) -> dict[str, int]:
    """单条 ``GROUP BY date`` 聚合，返回 {YYYY-MM-DD: 计数}；无数据的日期不出现。"""
    day = _utc_day(db, column)
    stmt = (
        select(day.label("day"), func.count())
        .where(column >= start, column < end, *conditions)
        .group_by(day)
    )
    rows = (await db.execute(stmt)).all()
    # PostgreSQL 返回 date、SQLite 返回字符串，统一截成 YYYY-MM-DD。
    return {str(row[0])[:10]: int(row[1]) for row in rows}


async def _scalar_count(db: AsyncSession, stmt) -> int:
    return int(await db.scalar(stmt) or 0)


async def analytics_overview(
    db: AsyncSession, days: int | None = None
) -> AnalyticsOverviewOut:
    """按日分桶的运营指标总览：固定查询数、补零、只读（§43）。

    ``days`` 缺省取 ``ANALYTICS_DEFAULT_WINDOW_DAYS``，上限
    ``ANALYTICS_MAX_WINDOW_DAYS``；越界抛 ``InvalidInput``，不静默截断，
    以免前端以为拿到的是请求的窗口。
    """
    window_days = settings.ANALYTICS_DEFAULT_WINDOW_DAYS if days is None else days
    max_days = settings.ANALYTICS_MAX_WINDOW_DAYS
    if window_days < 1 or window_days > max_days:
        raise InvalidInput(
            f"days 必须在 1 到 {max_days} 之间",
            details={"days": window_days, "max": max_days},
        )

    # 跨模块只读取表：模型在函数体内局部导入（docs/ARCHITECTURE.md §2）。
    from app.core.enums import ReviewStatus
    from app.modules.access.models import DownloadGrant
    from app.modules.cases.models import Case
    from app.modules.content.models import Resource, ResourceVersion, UploadSession
    from app.modules.identity.models import User
    from app.modules.interaction.models import (
        Comment,
        Correction,
        Favorite,
        Rating,
        ResourceRequest,
    )
    from app.modules.moderation.models import AuditLog, Report, Review

    now = datetime.now(timezone.utc)
    end_day = now.date()
    start_day = end_day - timedelta(days=window_days - 1)
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc)

    # (指标名, 时间列, 额外条件)。每条对应一次聚合查询，顺序即 METRIC_KEYS。
    sources = (
        ("new_users", User.created_at, (User.deleted_at.is_(None),)),
        ("new_resources", Resource.created_at, ()),
        (
            "published_versions",
            AuditLog.created_at,
            (AuditLog.action == "resource.published",),
        ),
        ("downloads", DownloadGrant.created_at, ()),
        ("favorites", Favorite.created_at, ()),
        ("ratings", Rating.updated_at, ()),
        ("comments", Comment.created_at, ()),
        ("reports", Report.created_at, ()),
        ("corrections", Correction.created_at, ()),
        ("requests", ResourceRequest.created_at, ()),
        ("cases", Case.created_at, ()),
    )

    buckets: dict[str, dict[str, int]] = {}
    for key, column, conditions in sources:
        buckets[key] = await _daily_counts(db, column, start, now, *conditions)

    # 补零：窗口内每天都出现，前端折线不断点。
    dates = [(start_day + timedelta(days=offset)).isoformat() for offset in range(window_days)]
    series = [
        AnalyticsPoint(
            date=date,
            **{key: buckets[key].get(date, 0) for key in METRIC_KEYS},
        )
        for date in dates
    ]

    totals = AnalyticsCounts(
        **{key: sum(buckets[key].get(date, 0) for date in dates) for key in METRIC_KEYS}
    )

    # --- 漏斗（窗口内绝对量） -------------------------------------------------
    uploaded = await _scalar_count(
        db,
        select(func.count())
        .select_from(UploadSession)
        .where(UploadSession.created_at >= start, UploadSession.created_at < now),
    )
    submitted = await _scalar_count(
        db,
        select(func.count())
        .select_from(ResourceVersion)
        .where(
            ResourceVersion.submitted_at.is_not(None),
            ResourceVersion.submitted_at >= start,
            ResourceVersion.submitted_at < now,
        ),
    )
    approved = await _scalar_count(
        db,
        select(func.count())
        .select_from(Review)
        .where(
            Review.status == ReviewStatus.APPROVED.value,
            Review.decided_at.is_not(None),
            Review.decided_at >= start,
            Review.decided_at < now,
        ),
    )
    # 发布阶段按资源去重：同一天先发布 v1 再回滚/重发 v2 仍只算一个资源。
    published = await _scalar_count(
        db,
        select(func.count(func.distinct(AuditLog.object_id))).where(
            AuditLog.action == "resource.published",
            AuditLog.created_at >= start,
            AuditLog.created_at < now,
        ),
    )
    funnel = AnalyticsFunnelOut(
        uploaded=uploaded,
        submitted=submitted,
        approved=approved,
        published=published,
        downloaded=totals.downloads,
    )

    # --- 下载 Top 课程（窗口内，最多 10 条） -----------------------------------
    from app.modules.catalog.models import Course

    download_total = func.count(DownloadGrant.id)
    top_rows = (
        await db.execute(
            select(Course.id, Course.name, download_total.label("download_count"))
            .select_from(Course)
            .join(Resource, Resource.course_id == Course.id)
            .join(DownloadGrant, DownloadGrant.resource_id == Resource.id)
            .where(DownloadGrant.created_at >= start, DownloadGrant.created_at < now)
            .group_by(Course.id, Course.name)
            .order_by(download_total.desc(), Course.name)
            .limit(TOP_COURSES_LIMIT)
        )
    ).all()
    top_courses = [
        TopCourseItem(course_id=row[0], name=row[1], download_count=int(row[2]))
        for row in top_rows
    ]

    return AnalyticsOverviewOut(
        window_days=window_days,
        generated_at=now,
        series=series,
        totals=totals,
        funnel=funnel,
        top_courses=top_courses,
        data_notes=list(DATA_NOTES),
    )
