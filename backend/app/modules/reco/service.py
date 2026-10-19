"""推荐与实验业务规则：行为采集、协同过滤、向量召回、学习排序与 A/B。

算法选择（不引入外部模型服务，全部在应用内可复现）：

- **协同过滤**：基于「共同交互」的 item-item 共现。取当前用户最近交互的资源，
  统计与其共同被同一批用户交互的其他资源，按共同用户数排序。冷启动时返回空。
- **向量召回**：资源特征 = 课程 + 类型 + 标签，构成稀疏向量；用户兴趣向量 =
  最近交互资源特征的加权平均；用余弦相似度召回。没有外部 embedding 服务。
- **学习排序**：逻辑回归（批量梯度下降，L2 正则），特征见 ``FEATURES``。
  训练样本来自行为事件：点击/完成/收藏为正，曝光未点击为负。模型参数存
  `recommendation_models`，可回退到默认权重。
- **A/B**：按 `sha256(experiment_key + subject)` 稳定分流；control 用规则排序，
  treatment 用模型排序；曝光与点击写行为事件，结果接口只做描述统计与 z 检验，
  **不自动切换线上策略**。

隐私与边界：
- 用户关闭采集后不再写入事件；历史事件按保留期清理，不用于自动处罚；
- 返回给前端的理由只说明「同课程 / 你最近看过 / 热门」等产品解释，不返回特征权重。
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete, func as sa_func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.enums import Role
from app.core.errors import InvalidInput, NotFound
from app.modules.reco.models import (
    BehaviorConsent,
    BehaviorEvent,
    BehaviorEventType,
    Experiment,
    ExperimentAssignment,
    ExperimentStatus,
    ReadingProgress,
    RecommendationModel,
)

__all__ = [
    "FEATURES",
    "assign_variant",
    "cleanup",
    "create_experiment",
    "experiment_results",
    "record_events",
    "recommend",
    "set_consent",
    "train_model",
    "upsert_progress",
]

FEATURES = [
    "bias",
    "cf",
    "vec",
    "same_course",
    "same_type",
    "hot",
    "fresh",
    "rating",
    "follow",
]

# 没有训练模型时的默认权重：以「同课程 / 向量 / 协同」为主，热度与新鲜度只做微调。
DEFAULT_WEIGHTS = {
    "bias": -0.8,
    "cf": 0.9,
    "vec": 1.2,
    "same_course": 1.6,
    "same_type": 0.5,
    "hot": 0.35,
    "fresh": 0.7,
    "rating": 0.45,
    "follow": 0.9,
}

POSITIVE_EVENTS = {
    BehaviorEventType.CLICK.value,
    BehaviorEventType.VIEW.value,
    BehaviorEventType.COMPLETE.value,
    BehaviorEventType.FAVORITE.value,
    BehaviorEventType.DOWNLOAD.value,
}

MAX_EVENTS_PER_REQUEST = 50
MAX_BATCH_PER_HOUR = 600
MAX_LIMIT = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _subject_key(user: Any, session_key: str | None) -> str:
    if user is not None:
        return f"user:{user.id}"
    if session_key:
        return f"session:{session_key[:64]}"
    return "session:anonymous"


# --------------------------------------------------------------------------- #
# 同意与采集
# --------------------------------------------------------------------------- #
async def consent_enabled(db: AsyncSession, *, user: Any) -> bool:
    """没有同意记录时按平台默认启用；显式关闭后立即停止采集。"""
    if user is None:
        return True
    row = (
        await db.execute(select(BehaviorConsent).where(BehaviorConsent.user_id == user.id))
    ).scalar_one_or_none()
    return True if row is None else bool(row.enabled)


async def set_consent(
    db: AsyncSession, *, user: Any, enabled: bool, reason: str | None = None
) -> BehaviorConsent:
    row = (
        await db.execute(select(BehaviorConsent).where(BehaviorConsent.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        row = BehaviorConsent(user_id=user.id, enabled=enabled, version="v1", reason=reason)
        db.add(row)
    else:
        row.enabled = enabled
        row.reason = (reason or None)
    await db.flush()
    return row


def _clamp_int(value: Any, *, low: int, high: int) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


async def record_events(
    db: AsyncSession,
    *,
    user: Any,
    session_key: str | None,
    events: Iterable[dict[str, Any]],
) -> int:
    """批量写入行为事件；返回实际写入条数。

    只接受白名单字段，客户端传来的额外字段被忽略；超出批量上限直接拒绝，
    避免一次请求写入海量事件。
    """
    if not await consent_enabled(db, user=user):
        return 0

    batch = list(events)
    if len(batch) > MAX_EVENTS_PER_REQUEST:
        raise InvalidInput(f"单次最多上报 {MAX_EVENTS_PER_REQUEST} 条行为事件")
    if not batch:
        return 0

    now = _now()
    window_start = now - timedelta(hours=1)
    key = f"behavior:{user.id}" if user is not None else f"behavior:{session_key or 'anon'}"
    recent = int(
        (
            await db.execute(
                select(sa_func.count(BehaviorEvent.id)).where(
                    BehaviorEvent.occurred_at >= window_start,
                    BehaviorEvent.user_id == (user.id if user is not None else None),
                    BehaviorEvent.session_key == (None if user is not None else session_key),
                )
            )
        ).scalar_one()
    )
    if recent + len(batch) > MAX_BATCH_PER_HOUR:
        raise InvalidInput("行为上报过于频繁，请稍后再试")

    written = 0
    for item in batch:
        event_type = str(item.get("event_type") or "")
        if event_type not in {member.value for member in BehaviorEventType}:
            continue
        resource_id = item.get("resource_id")
        if resource_id is not None:
            try:
                resource_id = uuid.UUID(str(resource_id))
            except (ValueError, TypeError):
                continue
        course_id = item.get("course_id")
        if course_id is not None:
            try:
                course_id = uuid.UUID(str(course_id))
            except (ValueError, TypeError):
                course_id = None
        db.add(
            BehaviorEvent(
                user_id=user.id if user is not None else None,
                session_key=None if user is not None else (session_key or None),
                event_type=event_type,
                resource_id=resource_id,
                course_id=course_id,
                surface=str(item.get("surface") or "")[:20] or None,
                position=_clamp_int(item.get("position"), low=0, high=200),
                dwell_ms=_clamp_int(item.get("dwell_ms"), low=0, high=3_600_000),
                progress_pct=_clamp_int(item.get("progress_pct"), low=0, high=100),
                experiment_key=str(item.get("experiment_key") or "")[:40] or None,
                variant=str(item.get("variant") or "")[:20] or None,
                occurred_at=now,
            )
        )
        written += 1

    if written:
        await _bump_assignment_counters(db, batch=batch)
        await db.flush()
    return written


async def _bump_assignment_counters(db: AsyncSession, *, batch: list[dict[str, Any]]) -> None:
    """同步更新实验分组的曝光/点击计数；找不到分组时静默跳过。"""
    counters: Counter[tuple[str, str]] = Counter()
    for item in batch:
        key = str(item.get("experiment_key") or "")
        variant = str(item.get("variant") or "")
        if not key or not variant:
            continue
        event_type = str(item.get("event_type") or "")
        if event_type == BehaviorEventType.IMPRESSION.value:
            counters[(key, variant, "exposure")] += 1
        elif event_type in POSITIVE_EVENTS:
            counters[(key, variant, "click")] += 1

    for (key, variant, field), count in counters.items():
        experiment_id = (
            await db.execute(select(Experiment.id).where(Experiment.key == key))
        ).scalar_one_or_none()
        if experiment_id is None:
            continue
        column = (
            ExperimentAssignment.exposure_count
            if field == "exposure"
            else ExperimentAssignment.click_count
        )
        await db.execute(
            update(ExperimentAssignment)
            .where(
                ExperimentAssignment.experiment_id == experiment_id,
                ExperimentAssignment.variant == variant,
            )
            .values({column: column + count})
        )


async def upsert_progress(
    db: AsyncSession,
    *,
    user: Any,
    resource_id: uuid.UUID,
    progress_pct: int,
    last_position: int | None = None,
    dwell_ms: int | None = None,
) -> ReadingProgress:
    """记录阅读进度；只增不减（回看不会把进度改小）。"""
    if not await consent_enabled(db, user=user):
        raise InvalidInput("你已关闭学习行为记录，无法保存阅读进度")
    pct = max(0, min(100, int(progress_pct)))
    row = (
        await db.execute(
            select(ReadingProgress).where(
                ReadingProgress.user_id == user.id,
                ReadingProgress.resource_id == resource_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ReadingProgress(
            user_id=user.id,
            resource_id=resource_id,
            progress_pct=pct,
            last_position=last_position,
            total_dwell_ms=max(0, int(dwell_ms or 0)),
            completed_at=_now() if pct >= 100 else None,
        )
        db.add(row)
    else:
        row.progress_pct = max(row.progress_pct, pct)
        if last_position is not None:
            row.last_position = last_position
        row.total_dwell_ms = int(row.total_dwell_ms or 0) + max(0, int(dwell_ms or 0))
        if row.progress_pct >= 100 and row.completed_at is None:
            row.completed_at = _now()
    await db.flush()
    return row


# --------------------------------------------------------------------------- #
# 候选与特征
# --------------------------------------------------------------------------- #
async def _recent_interactions(
    db: AsyncSession, *, subject: str, limit: int = 50
) -> list[tuple[uuid.UUID, uuid.UUID | None, str]]:
    """最近交互的 (resource_id, course_id, resource_type)，用于构造用户兴趣。"""
    if subject.startswith("user:"):
        condition = BehaviorEvent.user_id == uuid.UUID(subject.split(":", 1)[1])
    else:
        condition = BehaviorEvent.session_key == subject.split(":", 1)[1]
    rows = (
        await db.execute(
            select(BehaviorEvent.resource_id, BehaviorEvent.course_id, BehaviorEvent.event_type)
            .where(condition, BehaviorEvent.resource_id.is_not(None))
            .order_by(BehaviorEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        (row[0], row[1], row[2]) for row in rows if row[0] is not None and row[2] in POSITIVE_EVENTS
    ]


async def _candidate_pool(db: AsyncSession, *, user: Any, limit: int = 200) -> list[dict[str, Any]]:
    """可见且已发布的候选资源，带热度、评分与课程/类型信息。"""
    from app.modules.access.visibility import visible_resource_ids

    from sqlalchemy import DateTime, Integer, String, Uuid, column, table

    resources_table = table(
        "resources",
        column("id", Uuid),
        column("course_id", Uuid),
        column("owner_id", Uuid),
        column("published_version_id", Uuid),
        column("publication_status", String),
        column("resource_type", String),
        column("freshness_status", String),
        column("created_at", DateTime(timezone=True)),
        column("updated_at", DateTime(timezone=True)),
    )
    versions = table(
        "resource_versions",
        column("id", Uuid),
        column("resource_id", Uuid),
        column("title", String),
        column("review_status", String),
    )
    favorites = table(
        "favorites", column("resource_id", Uuid), column("id", Uuid)
    )
    ratings = table(
        "ratings", column("resource_id", Uuid), column("id", Uuid), column("score", Integer)
    )
    fav_counts = (
        select(favorites.c.resource_id, sa_func.count(favorites.c.id).label("favorites"))
        .group_by(favorites.c.resource_id)
        .subquery()
    )
    rating_stats = (
        select(
            ratings.c.resource_id,
            sa_func.count(ratings.c.id).label("rating_count"),
            sa_func.avg(ratings.c.score).label("rating_avg"),
        )
        .group_by(ratings.c.resource_id)
        .subquery()
    )
    r = resources_table.c
    query = (
        select(
            r.id,
            r.course_id,
            r.owner_id,
            r.resource_type,
            r.freshness_status,
            r.created_at,
            versions.c.title,
            sa_func.coalesce(fav_counts.c.favorites, 0).label("favorites"),
            sa_func.coalesce(rating_stats.c.rating_count, 0).label("rating_count"),
            sa_func.coalesce(rating_stats.c.rating_avg, 0.0).label("rating_avg"),
        )
        .select_from(
            resources_table.join(versions, versions.c.id == r.published_version_id)
            .outerjoin(fav_counts, fav_counts.c.resource_id == r.id)
            .outerjoin(rating_stats, rating_stats.c.resource_id == r.id)
        )
        .where(
            r.publication_status == "published",
            versions.c.review_status == "approved",
            versions.c.resource_id == r.id,
            r.id.in_(visible_resource_ids(user)),
        )
        .order_by(r.updated_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(query)).all()
    return [
        {
            "id": row[0],
            "course_id": row[1],
            "owner_id": row[2],
            "resource_type": row[3],
            "freshness_status": row[4],
            "created_at": row[5],
            "title": row[6],
            "favorites": int(row[7] or 0),
            "rating_count": int(row[8] or 0),
            "rating_avg": float(row[9] or 0.0),
        }
        for row in rows
    ]


async def _cf_scores(
    db: AsyncSession, *, subject: str, recent_ids: list[uuid.UUID], limit: int = 60
) -> dict[uuid.UUID, float]:
    """item-item 共现：与最近交互资源被同一批用户交互的其他资源。"""
    if not recent_ids:
        return {}
    if subject.startswith("user:"):
        user_id = uuid.UUID(subject.split(":", 1)[1])
        peers = (
            select(BehaviorEvent.user_id)
            .where(
                BehaviorEvent.resource_id.in_(recent_ids),
                BehaviorEvent.event_type.in_(POSITIVE_EVENTS),
                BehaviorEvent.user_id.is_not(None),
                BehaviorEvent.user_id != user_id,
            )
            .distinct()
            .limit(500)
            .subquery()
        )
        rows = (
            await db.execute(
                select(BehaviorEvent.resource_id, sa_func.count(sa_func.distinct(BehaviorEvent.user_id)))
                .where(
                    BehaviorEvent.user_id.in_(select(peers.c.user_id)),
                    BehaviorEvent.event_type.in_(POSITIVE_EVENTS),
                    BehaviorEvent.resource_id.is_not(None),
                    BehaviorEvent.resource_id.not_in(recent_ids),
                )
                .group_by(BehaviorEvent.resource_id)
                .order_by(sa_func.count(sa_func.distinct(BehaviorEvent.user_id)).desc())
                .limit(limit)
            )
        ).all()
    else:
        session_key = subject.split(":", 1)[1]
        peers = (
            select(BehaviorEvent.session_key)
            .where(
                BehaviorEvent.resource_id.in_(recent_ids),
                BehaviorEvent.event_type.in_(POSITIVE_EVENTS),
                BehaviorEvent.session_key.is_not(None),
                BehaviorEvent.session_key != session_key,
            )
            .distinct()
            .limit(500)
            .subquery()
        )
        rows = (
            await db.execute(
                select(BehaviorEvent.resource_id, sa_func.count(sa_func.distinct(BehaviorEvent.session_key)))
                .where(
                    BehaviorEvent.session_key.in_(select(peers.c.session_key)),
                    BehaviorEvent.event_type.in_(POSITIVE_EVENTS),
                    BehaviorEvent.resource_id.is_not(None),
                    BehaviorEvent.resource_id.not_in(recent_ids),
                )
                .group_by(BehaviorEvent.resource_id)
                .order_by(sa_func.count(sa_func.distinct(BehaviorEvent.session_key)).desc())
                .limit(limit)
            )
        ).all()
    if not rows:
        return {}
    top = max(float(row[1]) for row in rows) or 1.0
    return {row[0]: float(row[1]) / top for row in rows}


async def _tag_map(db: AsyncSession, *, resource_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    if not resource_ids:
        return {}
    from sqlalchemy import String, Uuid, column, table

    resource_tags = table("resource_tags", column("resource_id", Uuid), column("tag_id", Uuid))
    tags = table("tags", column("id", Uuid), column("name", String()))
    rows = (
        await db.execute(
            select(resource_tags.c.resource_id, tags.c.name)
            .select_from(resource_tags.join(tags, tags.c.id == resource_tags.c.tag_id))
            .where(resource_tags.c.resource_id.in_(resource_ids))
        )
    ).all()
    mapping: dict[uuid.UUID, set[str]] = defaultdict(set)
    for resource_id, name in rows:
        if name:
            mapping[resource_id].add(str(name))
    return mapping


def _vector(
    *, course_id: Any, resource_type: Any, tags: set[str]
) -> dict[str, float]:
    vector: dict[str, float] = {}
    if course_id:
        vector[f"course:{course_id}"] = 1.0
    if resource_type:
        vector[f"type:{resource_type}"] = 0.6
    for tag in tags:
        vector[f"tag:{tag}"] = 0.8
    return vector


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(key, 0.0) for key, value in a.items())
    if dot <= 0:
        return 0.0
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _features(
    db: AsyncSession,
    *,
    subject: str,
    candidates: list[dict[str, Any]],
    recent: list[tuple[uuid.UUID, uuid.UUID | None, str]],
    cf_scores: dict[uuid.UUID, float],
    follow_courses: set[uuid.UUID],
    follow_creators: set[uuid.UUID],
) -> list[tuple[dict[str, Any], dict[str, float]]]:
    tag_map = await _tag_map(db, resource_ids=[item["id"] for item in candidates])
    interest: dict[str, float] = defaultdict(float)
    for index, (resource_id, course_id, _event) in enumerate(recent[:20]):
        weight = 1.0 / (1 + index * 0.15)
        vector = _vector(
            course_id=course_id, resource_type=None, tags=tag_map.get(resource_id, set())
        )
        for key, value in vector.items():
            interest[key] += value * weight
    max_fav = max((item["favorites"] for item in candidates), default=0) or 1

    rows: list[tuple[dict[str, Any], dict[str, float]]] = []
    for item in candidates:
        vector = _vector(
            course_id=item["course_id"],
            resource_type=item["resource_type"],
            tags=tag_map.get(item["id"], set()),
        )
        same_course = 1.0 if any(item["course_id"] == course for _, course, _ in recent[:20]) else 0.0
        same_type = 1.0 if any(item["resource_type"] == event_type for _, _, event_type in recent[:20]) else 0.0
        features = {
            "bias": 1.0,
            "cf": float(cf_scores.get(item["id"], 0.0)),
            "vec": _cosine(interest, vector),
            "same_course": same_course,
            "same_type": same_type,
            "hot": float(item["favorites"]) / max_fav,
            "fresh": 1.0 if item["freshness_status"] == "current" else 0.0,
            "rating": float(item["rating_avg"]) / 5.0,
            "follow": 1.0
            if item["course_id"] in follow_courses or item["owner_id"] in follow_creators
            else 0.0,
        }
        rows.append((item, features))
    return rows


async def _follows(db: AsyncSession, *, user: Any) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    if user is None:
        return set(), set()
    from app.modules.interaction.models import Follow

    rows = (
        await db.execute(
            select(Follow.target_type, Follow.target_id).where(Follow.user_id == user.id)
        )
    ).all()
    courses = {row[1] for row in rows if row[0] == "course"}
    creators = {row[1] for row in rows if row[0] == "creator"}
    return courses, creators


def _score(features: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.get(name, 0.0) * features.get(name, 0.0) for name in FEATURES)
    # 转成 0—1 的可排序分，便于与规则分混合；不做概率解释。
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, total))))


def _reason(features: dict[str, float]) -> str:
    if features.get("same_course"):
        return "同课程的其他资料"
    if features.get("follow"):
        return "你关注的课程或作者"
    if features.get("cf", 0) > 0:
        return "看过这门课的人也在看"
    if features.get("vec", 0) > 0.2:
        return "与你的兴趣相关"
    if features.get("fresh"):
        return "近期更新的资料"
    return "热门资料"


# --------------------------------------------------------------------------- #
# 实验
# --------------------------------------------------------------------------- #
def _bucket(key: str, subject: str) -> int:
    digest = hashlib.sha256(f"{key}:{subject}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 10000


async def _running_experiment(db: AsyncSession) -> Experiment | None:
    return (
        await db.execute(
            select(Experiment)
            .where(Experiment.status == ExperimentStatus.RUNNING.value)
            .order_by(Experiment.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _pick_variant(experiment: Experiment, subject: str) -> str | None:
    variants = experiment.variants or []
    if not variants:
        return None
    bucket = _bucket(experiment.key, subject)
    if bucket >= int(experiment.traffic_bps or 0):
        return None  # 未进入实验，按 control 处理
    total = sum(int(item.get("weight_bps", 0)) for item in variants) or 1
    threshold = bucket % total
    cursor = 0
    for item in variants:
        cursor += int(item.get("weight_bps", 0))
        if threshold < cursor:
            return str(item.get("key") or "")
    return str(variants[-1].get("key") or "")


async def assign_variant(
    db: AsyncSession, *, experiment: Experiment, subject: str
) -> ExperimentAssignment | None:
    variant = _pick_variant(experiment, subject)
    if variant is None:
        return None
    row = (
        await db.execute(
            select(ExperimentAssignment).where(
                ExperimentAssignment.experiment_id == experiment.id,
                ExperimentAssignment.subject_key == subject,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ExperimentAssignment(
            experiment_id=experiment.id,
            subject_key=subject,
            variant=variant,
            assigned_at=_now(),
        )
        db.add(row)
        await db.flush()
    return row


async def create_experiment(
    db: AsyncSession,
    *,
    actor: Any,
    key: str,
    name: str,
    variants: list[dict[str, Any]],
    traffic_bps: int,
    metric: str,
    note: str | None,
) -> Experiment:
    clean_key = (key or "").strip()[:40]
    if not clean_key:
        raise InvalidInput("实验标识不能为空")
    if not variants or len(variants) < 2:
        raise InvalidInput("至少需要两个变体")
    total = sum(int(item.get("weight_bps", 0)) for item in variants)
    if total <= 0:
        raise InvalidInput("变体权重必须为正")
    if any(int(item.get("weight_bps", 0)) < 0 for item in variants):
        raise InvalidInput("变体权重不能为负")
    if not 0 < traffic_bps <= 10000:
        raise InvalidInput("流量比例必须在 (0, 10000] 区间")
    existing = (
        await db.execute(select(Experiment.id).where(Experiment.key == clean_key))
    ).scalar_one_or_none()
    if existing is not None:
        raise InvalidInput("实验标识已存在")

    row = Experiment(
        key=clean_key,
        name=name[:120],
        status=ExperimentStatus.DRAFT.value,
        variants=variants,
        metric=metric or "ctr",
        traffic_bps=int(traffic_bps),
        created_by=getattr(actor, "id", None),
        note=(note or None),
    )
    db.add(row)
    await db.flush()
    return row


async def start_experiment(db: AsyncSession, *, actor: Any, key: str) -> Experiment:
    row = (
        await db.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("实验不存在")
    if row.status == ExperimentStatus.RUNNING.value:
        return row
    row.status = ExperimentStatus.RUNNING.value
    row.started_at = _now()
    await db.flush()
    return row


async def stop_experiment(db: AsyncSession, *, actor: Any, key: str) -> Experiment:
    row = (
        await db.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("实验不存在")
    row.status = ExperimentStatus.STOPPED.value
    row.ended_at = _now()
    await db.flush()
    return row


def _z_test(exposures_a: int, clicks_a: int, exposures_b: int, clicks_b: int) -> dict[str, Any]:
    """两比例 z 检验；样本不足时返回 insufficient，不给显著性结论。"""
    if exposures_a < 30 or exposures_b < 30:
        return {"sufficient": False, "reason": "每组曝光不足 30，暂不判断显著性"}
    p_a = clicks_a / exposures_a
    p_b = clicks_b / exposures_b
    pooled = (clicks_a + clicks_b) / (exposures_a + exposures_b)
    if pooled <= 0 or pooled >= 1:
        return {"sufficient": False, "reason": "点击率为 0 或 1，无法计算"}
    se = math.sqrt(pooled * (1 - pooled) * (1 / exposures_a + 1 / exposures_b))
    z = (p_b - p_a) / se if se else 0.0
    # 双侧 95% 临界值 1.96；这里只给区间与是否越过临界值，不宣称因果。
    return {
        "sufficient": True,
        "z": round(z, 3),
        "significant_at_95": abs(z) >= 1.96,
        "ctr_a": round(p_a, 4),
        "ctr_b": round(p_b, 4),
        "lift": round((p_b - p_a) / p_a, 4) if p_a else None,
    }


async def experiment_results(db: AsyncSession, *, key: str) -> dict[str, Any]:
    experiment = (
        await db.execute(select(Experiment).where(Experiment.key == key))
    ).scalar_one_or_none()
    if experiment is None:
        raise NotFound("实验不存在")
    rows = (
        await db.execute(
            select(
                ExperimentAssignment.variant,
                sa_func.count(ExperimentAssignment.id),
                sa_func.coalesce(sa_func.sum(ExperimentAssignment.exposure_count), 0),
                sa_func.coalesce(sa_func.sum(ExperimentAssignment.click_count), 0),
            )
            .where(ExperimentAssignment.experiment_id == experiment.id)
            .group_by(ExperimentAssignment.variant)
        )
    ).all()
    variants = [
        {
            "variant": row[0],
            "subjects": int(row[1]),
            "exposures": int(row[2]),
            "clicks": int(row[3]),
        }
        for row in rows
    ]
    stats: dict[str, Any] | None = None
    if len(variants) >= 2:
        control = next((item for item in variants if item["variant"] == "control"), variants[0])
        treatment = next((item for item in variants if item is not control), variants[1])
        stats = _z_test(
            control["exposures"],
            control["clicks"],
            treatment["exposures"],
            treatment["clicks"],
        )
    return {
        "key": experiment.key,
        "status": experiment.status,
        "metric": experiment.metric,
        "traffic_bps": experiment.traffic_bps,
        "variants": variants,
        "comparison": stats,
        "note": "结果只做描述统计与区间判断；未达样本量或未显著时不自动切换线上策略。",
    }


# --------------------------------------------------------------------------- #
# 推荐
# --------------------------------------------------------------------------- #
async def _active_model(db: AsyncSession) -> RecommendationModel | None:
    return (
        await db.execute(
            select(RecommendationModel)
            .where(RecommendationModel.status == "active")
            .order_by(RecommendationModel.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def recommend(
    db: AsyncSession,
    *,
    user: Any,
    session_key: str | None = None,
    surface: str = "home",
    limit: int = 12,
    course_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """混合推荐：规则候选 → 协同过滤/向量特征 → 排序（模型或默认权重）→ 探索位。"""
    size = max(1, min(MAX_LIMIT, int(limit)))
    subject = _subject_key(user, session_key)
    recent = await _recent_interactions(db, subject=subject)
    recent_ids = list({row[0] for row in recent})
    candidates = await _candidate_pool(db, user=user, limit=240)
    if course_id is not None:
        candidates = [item for item in candidates if item["course_id"] == course_id]
    if not candidates:
        return {"items": [], "experiment": None, "model_version": None}

    cf_scores = await _cf_scores(db, subject=subject, recent_ids=recent_ids)
    follow_courses, follow_creators = await _follows(db, user=user)
    scored = await _features(
        db,
        subject=subject,
        candidates=candidates,
        recent=recent,
        cf_scores=cf_scores,
        follow_courses=follow_courses,
        follow_creators=follow_creators,
    )

    experiment = await _running_experiment(db)
    variant: str | None = None
    if experiment is not None:
        assignment = await assign_variant(db, experiment=experiment, subject=subject)
        variant = assignment.variant if assignment else None

    model = await _active_model(db)
    weights = DEFAULT_WEIGHTS
    model_version: int | None = None
    use_model = False
    if model is not None and variant not in (None, "control"):
        weights = {**DEFAULT_WEIGHTS, **(model.params or {})}
        model_version = model.version
        use_model = True
    elif model is not None and variant is None:
        # 没有运行中的实验时，active 模型直接生效；有实验且为 control 时保持规则排序。
        weights = {**DEFAULT_WEIGHTS, **(model.params or {})}
        model_version = model.version
        use_model = True

    if use_model:
        ranked = sorted(
            scored,
            key=lambda pair: _score(pair[1], weights),
            reverse=True,
        )
    else:
        # control：纯规则排序（同课程 → 关注 → 新鲜 → 热度 → 评分）。
        ranked = sorted(
            scored,
            key=lambda pair: (
                pair[1]["same_course"],
                pair[1]["follow"],
                pair[1]["fresh"],
                pair[1]["hot"],
                pair[1]["rating"],
            ),
            reverse=True,
        )

    seen = {row[0] for row in recent[:20]}
    # 已看内容降权但不排空：先排未看过的，再补已看过的，避免候选很少时首页为空。
    ordered = [pair for pair in ranked if pair[0]["id"] not in seen] + [
        pair for pair in ranked if pair[0]["id"] in seen
    ]
    items: list[dict[str, Any]] = []
    for item, features in ordered:
        items.append(
            {
                "resource_id": item["id"],
                "course_id": item["course_id"],
                "title": item["title"],
                "resource_type": item["resource_type"],
                "reason": _reason(features),
                "score": round(_score(features, weights), 4) if use_model else None,
            }
        )
        if len(items) >= size:
            break

    # 探索位：优先未看过的近期内容；没有合适候选时不占位。
    if len(items) >= 4:
        fresh_candidates = [
            item
            for item, features in ordered
            if features["fresh"] and item["id"] not in seen
            and item["id"] not in {entry["resource_id"] for entry in items}
        ]
        if fresh_candidates:
            pick = fresh_candidates[0]
            slot = min(2, len(items) - 1)
            items.insert(
                slot,
                {
                    "resource_id": pick["id"],
                    "course_id": pick["course_id"],
                    "title": pick["title"],
                    "resource_type": pick["resource_type"],
                    "reason": "新内容，供探索",
                    "score": None,
                },
            )
            items = items[:size]

    return {
        "items": items,
        "experiment": (
            {"key": experiment.key, "variant": variant} if experiment is not None else None
        ),
        "model_version": model_version,
        "surface": surface[:20],
    }


# --------------------------------------------------------------------------- #
# 训练
# --------------------------------------------------------------------------- #
async def _training_samples(
    db: AsyncSession, *, limit: int = 5000
) -> list[tuple[uuid.UUID, uuid.UUID, float]]:
    """构造 (user_id, resource_id, label) 样本。

    正样本：点击/完成/收藏/下载；负样本：曝光但同用户同资源没有任何正样本事件。
    """
    rows = (
        await db.execute(
            select(BehaviorEvent.user_id, BehaviorEvent.resource_id, BehaviorEvent.event_type)
            .where(
                BehaviorEvent.user_id.is_not(None),
                BehaviorEvent.resource_id.is_not(None),
                BehaviorEvent.event_type.in_(
                    list(POSITIVE_EVENTS) + [BehaviorEventType.IMPRESSION.value]
                ),
            )
            .order_by(BehaviorEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    positive: set[tuple[uuid.UUID, uuid.UUID]] = set()
    impressions: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for user_id, resource_id, event_type in rows:
        if event_type == BehaviorEventType.IMPRESSION.value:
            impressions.add((user_id, resource_id))
        elif event_type in POSITIVE_EVENTS:
            positive.add((user_id, resource_id))
    samples = [(user_id, resource_id, 1.0) for user_id, resource_id in positive]
    samples.extend(
        (user_id, resource_id, 0.0)
        for user_id, resource_id in impressions
        if (user_id, resource_id) not in positive
    )
    return samples


async def train_model(
    db: AsyncSession, *, actor: Any, epochs: int = 300, learning_rate: float = 0.05
) -> RecommendationModel:
    """训练逻辑回归排序模型；样本不足时明确拒绝，不产出空模型。"""
    samples = await _training_samples(db)
    if len(samples) < 50:
        raise InvalidInput("有效行为样本不足 50 条，暂不训练模型")

    # 特征按用户维度近似：用该用户最近交互的课程/类型构造兴趣，再与资源静态特征比对。
    users = {row[0] for row in samples}
    recent_by_user: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID | None, str]]] = {}
    for user_id in users:
        recent_by_user[user_id] = await _recent_interactions(
            db, subject=f"user:{user_id}", limit=20
        )
    candidates = await _candidate_pool(db, user=None, limit=400)
    candidate_map = {item["id"]: item for item in candidates}
    tag_map = await _tag_map(db, resource_ids=[item["id"] for item in candidates])
    max_fav = max((item["favorites"] for item in candidates), default=0) or 1

    rows: list[tuple[dict[str, float], float]] = []
    for user_id, resource_id, label in samples:
        item = candidate_map.get(resource_id)
        if item is None:
            continue
        recent = recent_by_user.get(user_id, [])
        interest: dict[str, float] = defaultdict(float)
        for index, (rid, cid, _event) in enumerate(recent):
            weight = 1.0 / (1 + index * 0.15)
            for key, value in _vector(
                course_id=cid, resource_type=None, tags=tag_map.get(rid, set())
            ).items():
                interest[key] += value * weight
        vector = _vector(
            course_id=item["course_id"],
            resource_type=item["resource_type"],
            tags=tag_map.get(resource_id, set()),
        )
        features = {
            "bias": 1.0,
            "cf": 0.0,  # 训练阶段不重复算共现，避免与线上推理口径不一致
            "vec": _cosine(interest, vector),
            "same_course": 1.0 if any(item["course_id"] == cid for _, cid, _ in recent) else 0.0,
            "same_type": 1.0 if any(item["resource_type"] == event for _, _, event in recent) else 0.0,
            "hot": float(item["favorites"]) / max_fav,
            "fresh": 1.0 if item["freshness_status"] == "current" else 0.0,
            "rating": float(item["rating_avg"]) / 5.0,
            "follow": 0.0,
        }
        rows.append((features, label))
    if len(rows) < 50:
        raise InvalidInput("可用于训练的样本不足（候选资源缺失），暂不训练")

    weights = {name: DEFAULT_WEIGHTS[name] * 0.1 for name in FEATURES}
    n = float(len(rows))
    for _ in range(epochs):
        gradients = {name: 0.0 for name in FEATURES}
        for features, label in rows:
            linear = sum(weights[name] * features[name] for name in FEATURES)
            prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, linear))))
            error = prediction - label
            for name in FEATURES:
                gradients[name] += error * features[name]
        for name in FEATURES:
            weights[name] -= learning_rate * (gradients[name] / n + 0.001 * weights[name])

    # 离线指标：训练集上的 logloss 与准确率，仅用于观察是否收敛，不作为上线依据。
    logloss = 0.0
    correct = 0
    for features, label in rows:
        linear = sum(weights[name] * features[name] for name in FEATURES)
        p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, linear))))
        p = min(1 - 1e-9, max(1e-9, p))
        logloss -= label * math.log(p) + (1 - label) * math.log(1 - p)
        correct += int((p >= 0.5) == (label >= 0.5))

    latest = (
        await db.execute(select(sa_func.max(RecommendationModel.version)))
    ).scalar_one_or_none() or 0
    model = RecommendationModel(
        version=int(latest) + 1,
        kind="logistic",
        status="trained",
        params={name: round(weights[name], 6) for name in FEATURES},
        metrics={
            "samples": len(rows),
            "positive_rate": round(sum(label for _, label in rows) / len(rows), 4),
            "logloss": round(logloss / len(rows), 4),
            "accuracy": round(correct / len(rows), 4),
        },
        sample_size=len(rows),
        trained_at=_now(),
        created_by=getattr(actor, "id", None),
    )
    db.add(model)
    await db.flush()
    return model


async def activate_model(db: AsyncSession, *, actor: Any, version: int) -> RecommendationModel:
    model = (
        await db.execute(
            select(RecommendationModel).where(RecommendationModel.version == version)
        )
    ).scalar_one_or_none()
    if model is None:
        raise NotFound("模型版本不存在")
    await db.execute(
        update(RecommendationModel)
        .where(RecommendationModel.status == "active")
        .values(status="archived")
    )
    model.status = "active"
    await db.flush()
    return model


async def cleanup(db: AsyncSession, *, retention_days: int | None = None) -> int:
    """删除超过保留期的行为事件；不动阅读进度与实验分组。"""
    days = max(1, int(retention_days or settings.BEHAVIOR_RETENTION_DAYS))
    cutoff = _now() - timedelta(days=days)
    result = await db.execute(
        delete(BehaviorEvent)
        .where(BehaviorEvent.occurred_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def delete_user_behavior(db: AsyncSession, *, user_id: uuid.UUID) -> None:
    """账号注销时删除该用户的行为数据与进度（供 identity 注销流程调用）。"""
    await db.execute(
        delete(BehaviorEvent)
        .where(BehaviorEvent.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(ReadingProgress)
        .where(ReadingProgress.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(BehaviorConsent)
        .where(BehaviorConsent.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
