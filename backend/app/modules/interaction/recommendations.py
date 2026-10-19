"""P2 规则推荐：先过滤可见性，再以可解释的规则取前 N 条。

在规则结果中保留一个固定探索槽位，让新内容有曝光机会，同时不改变规则项之间的排序契约
（专业相关 → 关注命中 → 现行状态 → 收缩评分 → 收藏数 → 更新时间 → 资源 ID）。

专业相关（beta14）：查看者 user_profiles 的 major_id 命中其培养方案（curricula →
curriculum_courses）关联课程的资源排最前，理由「与你专业相关」；仅 college_id 命中
（同学院其他专业方案）次之。无 profile / 无 major / 方案无覆盖时该键恒为 0，
排序与理由回退既有逻辑，不报错。

探索槽位参数（**待确认的默认值**，集中在此处便于后续调整）：
- 仅当请求条数 ``limit >= EXPLORE_ENABLED_MIN_LIMIT`` 时启用，占 1 个槽位（最多 1/4）；
- 候选须已通过可见性过滤、非已过时，且满足「新内容」：``created_at`` 在 30 天内且评价数 < 3；
- 插入位置为第 3 条（1-based），不与规则项重复；无合格候选时不占位、不留空。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    Uuid,
    case,
    column,
    exists,
    false,
    func,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.interaction.models import Favorite, Follow, Rating
from app.modules.interaction.schemas import RecommendationItem, RecommendationsOut
from app.modules.interaction.service import (
    RATING_CONFIDENCE_C,
    RATING_PRIOR,
    _course_brief_map,
    _summarize,
)

# --- 探索槽位（待确认的默认值）---
EXPLORE_ENABLED_MIN_LIMIT = 4
EXPLORE_SLOT_COUNT = 1
EXPLORE_SLOT_POSITION = 3  # 1-based
EXPLORE_MAX_AGE_DAYS = 30
EXPLORE_MAX_RATING_COUNT = 3
EXPLORE_REASON = "新内容，供探索"

# --- 专业相关信号（beta14）：只读引用 identity/catalog 的表，不导入对方模型类 ---
_PROFILES = table(
    "user_profiles",
    column("user_id", Uuid),
    column("major_id", Uuid),
    column("college_id", Uuid),
    column("enrollment_year", Integer),
)
_CURRICULA = table(
    "curricula",
    column("id", Uuid),
    column("major_id", Uuid),
    column("entry_year", Integer),
    column("status", String),
)
_CURRICULUM_COURSES = table(
    "curriculum_courses", column("curriculum_id", Uuid), column("course_id", Uuid)
)
_MAJORS = table("majors", column("id", Uuid), column("college_id", Uuid))

# 专业信号权重：major 精确命中 > 仅 college 命中 > 无信号。
_MAJOR_MATCH_WEIGHT = 2
_COLLEGE_MATCH_WEIGHT = 1


async def _major_signal(db: AsyncSession, *, user, resources):
    """构建「专业相关」排序信号：major 命中培养方案课程 > college 命中。

    返回 (major_case 表达式, major_hit 可选标签, college_hit 可选标签)；
    无 profile / 无 major / 方案无覆盖时返回 None，调用方保持原排序不变。
    """
    if user is None:
        return None
    profile = (
        await db.execute(
            select(
                _PROFILES.c.major_id,
                _PROFILES.c.college_id,
                _PROFILES.c.enrollment_year,
            ).where(_PROFILES.c.user_id == user.id)
        )
    ).first()
    if profile is None or (profile.major_id is None and profile.college_id is None):
        return None
    r = resources.c

    def _course_in_curricula(curricula_ids):
        return exists(
            select(_CURRICULUM_COURSES.c.course_id).where(
                _CURRICULUM_COURSES.c.curriculum_id.in_(curricula_ids),
                _CURRICULUM_COURSES.c.course_id == r.course_id,
            )
        ).correlate(resources)

    major_hit = college_hit = None
    if profile.major_id is not None:
        year_condition = true()
        if profile.enrollment_year is not None:
            # 入学年级已知时按年级取方案；方案未记录年级（待目录确认）时不排除。
            year_condition = or_(
                _CURRICULA.c.entry_year.is_(None),
                _CURRICULA.c.entry_year == profile.enrollment_year,
            )
        curricula_ids = select(_CURRICULA.c.id).where(
            _CURRICULA.c.major_id == profile.major_id,
            _CURRICULA.c.status == "active",
            year_condition,
        )
        major_hit = _course_in_curricula(curricula_ids)
    if profile.college_id is not None:
        college_curricula_ids = select(_CURRICULA.c.id).where(
            _CURRICULA.c.status == "active",
            _CURRICULA.c.major_id.in_(
                select(_MAJORS.c.id).where(_MAJORS.c.college_id == profile.college_id)
            ),
        )
        college_hit = _course_in_curricula(college_curricula_ids)
    if major_hit is None and college_hit is None:
        return None
    if major_hit is not None and college_hit is not None:
        signal = case(
            (major_hit, _MAJOR_MATCH_WEIGHT),
            (college_hit, _COLLEGE_MATCH_WEIGHT),
            else_=0,
        )
    elif major_hit is not None:
        signal = case((major_hit, _MAJOR_MATCH_WEIGHT), else_=0)
    else:
        signal = case((college_hit, _COLLEGE_MATCH_WEIGHT), else_=0)
    return signal, major_hit, college_hit


async def recommend_resources(
    db: AsyncSession, *, user=None, course_id: uuid.UUID | None = None, limit: int = 6,
) -> RecommendationsOut:
    from app.modules.access.visibility import visible_resource_ids

    resources = table(
        "resources", column("id", Uuid), column("course_id", Uuid),
        column("owner_id", Uuid), column("published_version_id", Uuid),
        column("publication_status", String), column("resource_type", String),
        column("freshness_status", String), column("updated_at", DateTime(timezone=True)),
        column("created_at", DateTime(timezone=True)),
    )
    versions = table(
        "resource_versions", column("id", Uuid), column("resource_id", Uuid),
        column("title", String), column("review_status", String),
    )
    ratings = select(
        Rating.resource_id, func.count(Rating.id).label("rating_count"),
        func.avg(Rating.score).label("average"),
    ).group_by(Rating.resource_id).subquery()
    favorites = select(
        Favorite.resource_id, func.count(Favorite.id).label("favorite_count"),
    ).group_by(Favorite.resource_id).subquery()
    r = resources.c
    count = func.coalesce(ratings.c.rating_count, 0)
    average = func.coalesce(ratings.c.average, 0.0)
    shrunk = (count * average + RATING_CONFIDENCE_C * RATING_PRIOR) / (count + RATING_CONFIDENCE_C)
    favorite_count = func.coalesce(favorites.c.favorite_count, 0)
    course_follow = creator_follow = false()
    if user is not None:
        course_follow = exists(select(Follow.id).where(
            Follow.user_id == user.id, Follow.target_type == "course", Follow.target_id == r.course_id,
        )).correlate(resources)
        creator_follow = exists(select(Follow.id).where(
            Follow.user_id == user.id, Follow.target_type == "creator", Follow.target_id == r.owner_id,
        )).correlate(resources)
    interest = case((or_(course_follow, creator_follow), 1), else_=0)
    freshness = case((r.freshness_status == "current", 0), else_=1)
    query = select(
        r.id, r.course_id, r.resource_type, r.freshness_status, r.published_version_id,
        versions.c.title, count.label("rating_count"), average.label("average"),
        favorite_count.label("favorite_count"),
        course_follow.label("course_follow"), creator_follow.label("creator_follow"),
    ).select_from(
        resources.join(versions, versions.c.id == r.published_version_id)
        .outerjoin(ratings, ratings.c.resource_id == r.id)
        .outerjoin(favorites, favorites.c.resource_id == r.id)
    ).where(
        r.publication_status == "published", versions.c.review_status == "approved",
        versions.c.resource_id == r.id, r.id.in_(visible_resource_ids(user)),
        r.freshness_status != "outdated",
    )
    if course_id is not None:
        query = query.where(r.course_id == course_id)
    # 专业相关作为一级排序键置于最前；无信号时 query 与排序保持不变（回退既有逻辑）。
    signal = await _major_signal(db, user=user, resources=resources)
    if signal is not None:
        major_case, major_hit, college_hit = signal
        query = query.add_columns(
            (major_hit if major_hit is not None else false()).label("major_hit"),
            (college_hit if college_hit is not None else false()).label("college_hit"),
        )
    ordering = [freshness, shrunk.desc(), favorite_count.desc(), r.updated_at.desc(), r.id]
    if user is not None:
        ordering.insert(0, interest.desc())
    if signal is not None:
        ordering.insert(0, major_case.desc())
    rows = list((await db.execute(query.order_by(*ordering).limit(limit))).all())
    courses = await _course_brief_map(db, [row.course_id for row in rows])
    items = [_item(row, courses, course_id) for row in rows]

    # 固定探索槽位：占用 1 个槽位，不破坏规则项之间的相对顺序。
    if limit >= EXPLORE_ENABLED_MIN_LIMIT and EXPLORE_SLOT_COUNT > 0:
        explore_row = await _explore_row(
            db, query, resources=resources, rating_count=count,
            exclude_ids=[row.id for row in rows],
        )
        if explore_row is not None:
            if explore_row.course_id not in courses:
                courses.update(await _course_brief_map(db, [explore_row.course_id]))
            items.insert(
                min(EXPLORE_SLOT_POSITION - 1, len(items)),
                _item(explore_row, courses, course_id, explore=True),
            )
            items = items[:limit]
    return RecommendationsOut(items=items)


def _item(row, courses, course_id, *, explore: bool = False) -> RecommendationItem:
    """由查询行构造推荐项；探索项理由以「新内容，供探索」明确标注，便于用户区分。"""
    reasons: list[str] = []
    if explore:
        reasons.append(EXPLORE_REASON)
    else:
        # 专业信号为一级键，理由置于最前；无该列（无 profile 等）时静默跳过。
        if getattr(row, "major_hit", None):
            reasons.append("与你专业相关")
        elif getattr(row, "college_hit", None):
            reasons.append("与你学院相关")
        if row.course_follow:
            reasons.append("来自你关注的课程")
        if row.creator_follow:
            reasons.append("来自你关注的贡献者")
        if row.freshness_status == "current":
            reasons.append("现行内容")
        if row.rating_count:
            reasons.append(f"{row.rating_count} 条评价，评分已按样本量收缩")
        if row.favorite_count:
            reasons.append(f"{row.favorite_count} 人收藏")
    if course_id is not None:
        reasons.append("这门课的资料")
    if not reasons:
        reasons.append("按更新时间推荐")
    return RecommendationItem(
        resource_id=row.id, course_id=row.course_id, course=courses.get(row.course_id),
        title=row.title, resource_type=row.resource_type, freshness_status=row.freshness_status,
        published_version_id=row.published_version_id,
        rating=_summarize(row.rating_count, float(row.average)), reasons=reasons, explore=explore,
    )


async def _explore_row(
    db: AsyncSession, query, *, resources, rating_count, exclude_ids: list[uuid.UUID],
):
    """挑选一个「新内容」候选：复用规则查询的全部过滤条件，只追加探索约束。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=EXPLORE_MAX_AGE_DAYS)
    stmt = query.where(
        resources.c.created_at >= cutoff,
        rating_count < EXPLORE_MAX_RATING_COUNT,
    )
    if exclude_ids:
        stmt = stmt.where(resources.c.id.notin_(exclude_ids))
    stmt = stmt.order_by(resources.c.created_at.desc(), resources.c.id).limit(EXPLORE_SLOT_COUNT)
    rows = (await db.execute(stmt)).all()
    return rows[0] if rows else None
