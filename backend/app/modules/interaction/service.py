"""interaction 应用服务（收藏、评价、关注、评论、纠错、求资料、搜索）。

业务规则的唯一落点；router 只做解析与调用（docs/ARCHITECTURE.md §1）。
跨模块服务在函数体内局部导入，跨模块表用字符串外键或只读 SQL 访问，不导入对方模型类。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import (
    DateTime,
    String,
    Text,
    and_,
    bindparam,
    column,
    delete,
    func,
    or_,
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.enums import (
    ContributionKind,
    CorrectionStatus,
    DomainEventType,
    RequestStatus,
    ResourceType,
)
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.events import emit_event
from app.core.permissions import COMMENT_MODERATE, effective_permissions
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.modules.interaction.models import (
    Comment,
    Correction,
    Favorite,
    Follow,
    FollowTargetType,
    Rating,
    ResourceRequest,
    ResourceRequestFollow,
)
from app.modules.interaction.schemas import (
    CommentItem,
    CorrectionItem,
    FavoriteItem,
    FollowItem,
    FollowResult,
    RatingSummary,
    RequestCreateResult,
    RequestItem,
    SearchItem,
)

# --- 评分收缩（§20.1）---
# 先验 prior 取 1-5 星区间的中点 3.5，表示"未知质量时保持中性"。
# 置信度 C 取 5：约 5 条评价后才让单条五星明显接近极值，避免单个五星无条件置顶。
# 公式：(count * average + C * prior) / (count + C)，可解释、单调且可从小样本平滑过渡。
RATING_PRIOR = 3.5
RATING_CONFIDENCE_C = 5.0

_WHITESPACE = re.compile(r"\s+")
_LIKE_ESCAPE = "\\"

# 资料需求榜（sort=hot）只统计近 7 天创建的开放需求：超过窗口的需求自然沉底，
# 避免“关注数最高的老需求”长期占据榜单（beta14 排序与推荐信号）。
REQUEST_HOT_WINDOW_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: str) -> str:
    """标准化查询：去首尾空白并折叠内部空白（§19.1）。"""
    return _WHITESPACE.sub(" ", value.strip())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_uuid(value: Any) -> uuid.UUID:
    """把裸 SQL 读回的 uuid 列值统一成 UUID 对象。

    PostgreSQL 的 native uuid 列由驱动返回 UUID；SQLite 以 CHAR(32) 存储无连字符
    字符串，读回是 str。下游（如 expanding bindparam 的 Uuid 类型）要求 UUID 对象。
    """
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _like_fragments(db: AsyncSession) -> tuple[str, str]:
    """按方言返回 (大小写不敏感匹配运算符, 年份提取表达式)。

    PostgreSQL 用 `ILIKE` / `EXTRACT`；SQLite（本地开发与测试库）没有这两个语法，
    这里用等价物替换：SQLite 的 `LIKE` 对 ASCII 本就大小写不敏感（中文无大小写），
    年份用 `strftime` 提取后再 CAST 成整数，行为与 `EXTRACT` 一致。
    """
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else "postgresql"
    if dialect == "sqlite":
        return "LIKE", "CAST(strftime('%Y', start_date) AS INTEGER)"
    return "ILIKE", "EXTRACT(YEAR FROM start_date)"


# =====================================================================
# 评价（§20.1、§29 幂等）
# =====================================================================


async def has_access_history(
    db: AsyncSession, *, user_id: uuid.UUID, resource_id: uuid.UUID
) -> bool:
    """评价资格：至少获得过该内容访问权（§20.1）。

    依据 download_grants / preview_grants 的实际发放记录判断，避免未接触内容的匿名刷分。
    access 模块未声明"查询历史"接口，这里只读其表，不导入对方模型类。
    """
    from sqlalchemy import Uuid

    stmt = text(
        "SELECT 1 FROM download_grants WHERE user_id = :uid AND resource_id = :rid "
        "UNION ALL "
        "SELECT 1 FROM preview_grants WHERE user_id = :uid AND resource_id = :rid "
        "LIMIT 1"
    ).bindparams(
        # UUID 参数声明类型：SQLite 的 DBAPI 不接受 uuid.UUID 对象（见 search() 内注释）。
        bindparam("uid", type_=Uuid()),
        bindparam("rid", type_=Uuid()),
    )
    row = (await db.execute(stmt, {"uid": user_id, "rid": resource_id})).first()
    return row is not None


def _summarize(count: int, average: float) -> RatingSummary:
    shrunk = (count * average + RATING_CONFIDENCE_C * RATING_PRIOR) / (count + RATING_CONFIDENCE_C)
    return RatingSummary(average=round(average, 4), count=count, shrunk=round(shrunk, 4))


async def resource_rating_summary(
    db: AsyncSession, resource_id: uuid.UUID
) -> RatingSummary:
    """§20.1 评分聚合。结果由 ratings 业务记录重算（§15.2），不依赖冗余列。"""
    row = (
        await db.execute(
            select(func.count(Rating.id), func.avg(Rating.score)).where(
                Rating.resource_id == resource_id
            )
        )
    ).one()
    count = int(row[0] or 0)
    average = float(row[1]) if row[1] is not None else 0.0
    return _summarize(count, average)


async def _rating_summaries(
    db: AsyncSession, resource_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RatingSummary]:
    """批量聚合，避免搜索结果逐条查询（N+1）。"""
    if not resource_ids:
        return {}
    rows = await db.execute(
        select(Rating.resource_id, func.count(Rating.id), func.avg(Rating.score))
        .where(Rating.resource_id.in_(resource_ids))
        .group_by(Rating.resource_id)
    )
    result: dict[uuid.UUID, RatingSummary] = {}
    for resource_id, count, average in rows:
        result[resource_id] = _summarize(int(count or 0), float(average) if average is not None else 0.0)
    return result


async def submit_rating(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    score: int,
    request_id: str,
) -> RatingSummary:
    """提交或更新本人对指定版本的评分。

    (user_id, version_id) 唯一：重试只更新同一条记录，评价人数不重复累计（§29 A17）。
    历史变更通过审计记录留痕（§20.1 可追溯）。
    """
    from app.modules.content.service import get_resource, get_version

    await get_resource(db, resource_id)
    version = await get_version(db, version_id)
    if version.resource_id != resource_id:
        raise InvalidInput("版本不属于该资源", details={"version_id": str(version_id)})

    if not await has_access_history(db, user_id=user_id, resource_id=resource_id):
        raise Forbidden(
            "需要先获得该资料的访问权才能评价",
            code="RATING_NOT_ELIGIBLE",
        )

    existing = (
        await db.execute(
            select(Rating).where(Rating.user_id == user_id, Rating.version_id == version_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        rating = Rating(
            user_id=user_id, resource_id=resource_id, version_id=version_id, score=score
        )
        db.add(rating)
        await db.flush()
        await record_audit(
            db,
            action="rating.create",
            object_type="rating",
            object_id=rating.id,
            actor_id=user_id,
            after={"score": score, "version_id": str(version_id)},
            request_id=request_id,
        )
    elif existing.score != score:
        before = existing.score
        existing.score = score
        await record_audit(
            db,
            action="rating.update",
            object_type="rating",
            object_id=existing.id,
            actor_id=user_id,
            before={"score": before},
            after={"score": score},
            request_id=request_id,
        )
    # 分数未变则幂等无操作。

    return await resource_rating_summary(db, resource_id)


# =====================================================================
# 收藏（§20.1、§29 幂等）
# =====================================================================


async def set_favorite(
    db: AsyncSession, *, user_id: uuid.UUID, resource_id: uuid.UUID
) -> None:
    """PUT 设置收藏：唯一约束 + ON CONFLICT DO NOTHING，重试不反转状态（§25.1 A17）。"""
    from app.modules.content.service import assert_visible, get_resource

    resource = await get_resource(db, resource_id)
    await assert_visible(db, resource)
    await db.execute(
        pg_insert(Favorite)
        .values(user_id=user_id, resource_id=resource_id)
        .on_conflict_do_nothing(index_elements=["user_id", "resource_id"])
    )


async def remove_favorite(
    db: AsyncSession, *, user_id: uuid.UUID, resource_id: uuid.UUID
) -> None:
    """DELETE 取消收藏：删除天然幂等，重复调用结果一致（§25.1）。"""
    await db.execute(
        delete(Favorite).where(
            Favorite.user_id == user_id, Favorite.resource_id == resource_id
        )
    )


async def list_favorites(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
) -> CursorPage[FavoriteItem]:
    """§20.1 收藏列表，包含资源当前可用性（下架显示不可用，不打开旧版文件）。"""
    from app.core.errors import NotFound as _NotFound
    from app.modules.content.service import get_resource, published_version

    page_size = normalize_page_size(limit)
    stmt = select(Favorite).where(Favorite.user_id == user_id)
    decoded = _decode_time_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                Favorite.created_at < created_at,
                (Favorite.created_at == created_at) & (Favorite.id < last_id),
            )
        )
    stmt = stmt.order_by(Favorite.created_at.desc(), Favorite.id.desc()).limit(page_size + 1)
    rows = list((await db.execute(stmt)).scalars().all())

    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items: list[FavoriteItem] = []
    for favorite in rows:
        available = False
        publication_status = "unavailable"
        course_id: uuid.UUID | None = None
        title: str | None = None
        try:
            resource = await get_resource(db, favorite.resource_id)
            publication_status = str(resource.publication_status)
            course_id = resource.course_id
            available = publication_status == "published"
            version = await published_version(db, resource)
            if version is not None:
                title = version.title
        except _NotFound:
            # 资源已不可访问时仍保留收藏条目，仅标记不可用。
            pass
        items.append(
            FavoriteItem(
                resource_id=favorite.resource_id,
                created_at=favorite.created_at,
                available=available,
                publication_status=publication_status,
                course_id=course_id,
                title=title,
            )
        )

    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if has_more and rows
        else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


# =====================================================================
# 关注（§20.2）
# =====================================================================


async def _assert_follow_target(
    db: AsyncSession, target_type: FollowTargetType, target_id: uuid.UUID
) -> None:
    if target_type is FollowTargetType.COURSE:
        from app.modules.catalog.service import get_course

        await get_course(db, target_id)
    else:
        from app.modules.identity.service import get_user

        await get_user(db, target_id)


async def set_follow(
    db: AsyncSession, *, user_id: uuid.UUID, target_type: str, target_id: uuid.UUID
) -> None:
    try:
        follow_type = FollowTargetType(target_type)
    except ValueError as exc:
        raise InvalidInput("关注对象类型无效", details={"target_type": target_type}) from exc
    await _assert_follow_target(db, follow_type, target_id)
    await db.execute(
        pg_insert(Follow)
        .values(user_id=user_id, target_type=follow_type.value, target_id=target_id)
        .on_conflict_do_nothing(index_elements=["user_id", "target_type", "target_id"])
    )


async def remove_follow(
    db: AsyncSession, *, user_id: uuid.UUID, target_type: str, target_id: uuid.UUID
) -> None:
    try:
        follow_type = FollowTargetType(target_type)
    except ValueError as exc:
        raise InvalidInput("关注对象类型无效", details={"target_type": target_type}) from exc
    await db.execute(
        delete(Follow).where(
            Follow.user_id == user_id,
            Follow.target_type == follow_type.value,
            Follow.target_id == target_id,
        )
    )


async def list_follows(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_type: str | None,
    cursor: str | None,
    limit: int | None,
) -> CursorPage[FollowItem]:
    page_size = normalize_page_size(limit)
    stmt = select(Follow).where(Follow.user_id == user_id)
    if target_type is not None:
        try:
            stmt = stmt.where(Follow.target_type == FollowTargetType(target_type).value)
        except ValueError as exc:
            raise InvalidInput("关注对象类型无效", details={"target_type": target_type}) from exc
    decoded = _decode_time_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                Follow.created_at < created_at,
                (Follow.created_at == created_at) & (Follow.id < last_id),
            )
        )
    stmt = stmt.order_by(Follow.created_at.desc(), Follow.id.desc()).limit(page_size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items = [FollowItem(
        id=follow.id,
        target_type=FollowTargetType(follow.target_type),
        target_id=follow.target_id,
        created_at=follow.created_at,
        target=await _follow_target_brief(db, follow.target_type, follow.target_id),
    ) for follow in rows]

    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if has_more and rows
        else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def _follow_target_brief(
    db: AsyncSession, target_type: str, target_id: uuid.UUID
) -> dict[str, Any] | None:
    try:
        if target_type == FollowTargetType.COURSE.value:
            from app.modules.catalog.service import course_brief

            brief = await course_brief(db, target_id)
            return brief.model_dump() if hasattr(brief, "model_dump") else dict(brief)
        from app.modules.identity.service import display_name_of, get_user

        user = await get_user(db, target_id)
        return {"user_id": str(target_id), "display_name": display_name_of(user)}
    except NotFound:
        # 目标被合并或注销时返回 None，不泄露内部状态。
        return None


# =====================================================================
# 评论（§20.2）
# =====================================================================


async def list_comments(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
    user=None,
) -> CursorPage[CommentItem]:
    await assert_comment_resource(db, resource_id=resource_id, user=user)

    page_size = normalize_page_size(limit)
    stmt = select(Comment).where(Comment.resource_id == resource_id)
    decoded = _decode_time_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                Comment.created_at > created_at,
                (Comment.created_at == created_at) & (Comment.id > last_id),
            )
        )
    stmt = stmt.order_by(Comment.created_at.asc(), Comment.id.asc()).limit(page_size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items = await _comment_items(db, rows, viewer=user)
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if has_more and rows
        else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def create_comment(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    body: str,
    parent_id: uuid.UUID | None,
) -> CommentItem:
    """新增评论或回复。body 以纯文本存储，不作为任意 HTML 执行（§28）。"""
    from app.core.enums import AccountStatus
    from app.modules.identity.service import get_user

    user = await get_user(db, user_id)
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能发表评论")
    await assert_comment_resource(db, resource_id=resource_id, user=user)

    if parent_id is not None:
        parent = (
            await db.execute(select(Comment).where(Comment.id == parent_id)
                             .with_for_update().execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if parent is None or parent.resource_id != resource_id:
            raise InvalidInput("回复目标无效", details={"parent_id": str(parent_id)})
        if parent.withdrawn_at is not None or parent.is_hidden:
            raise Conflict("该评论已撤回或隐藏，不能继续回复")
        if parent.parent_id is not None:
            raise InvalidInput("仅支持回复主评论")

    comment = Comment(
        resource_id=resource_id, user_id=user_id, parent_id=parent_id, body=body
    )
    db.add(comment)
    await db.flush()
    items = await _comment_items(db, [comment])
    return items[0]


async def _comment_items(
    db: AsyncSession, comments: list[Comment], viewer=None
) -> list[CommentItem]:
    from app.modules.identity.service import display_name_of, get_users

    if not comments:
        return []
    users = await get_users(db, [c.user_id for c in comments])
    hidden = [c for c in comments if c.is_hidden and c.withdrawn_at is None]
    reasons = await _hide_reasons(db, [c.id for c in hidden]) if hidden else {}
    items: list[CommentItem] = []
    can_moderate = viewer is not None and COMMENT_MODERATE in await effective_permissions(db, viewer)
    for comment in comments:
        user = users.get(comment.user_id)
        name = display_name_of(user) if user is not None else "已注销用户"
        moderation_reason = None
        if viewer is not None and comment.is_hidden and comment.withdrawn_at is None:
            if viewer.id == comment.user_id or can_moderate:
                moderation_reason = reasons.get(comment.id)
        items.append(
            CommentItem(
                id=comment.id,
                resource_id=comment.resource_id,
                user_id=comment.user_id,
                display_name=name,
                parent_id=comment.parent_id,
                body=comment.body if comment.withdrawn_at is None and not comment.is_hidden else None,
                created_at=comment.created_at,
                status=comment_status(comment),
                revision=comment.revision,
                moderation_reason=moderation_reason,
            )
        )
    return items


async def _hide_reasons(
    db: AsyncSession, comment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """批量读取评论最近一次被隐藏的处理理由。

    只读 moderation 拥有的 `audit_logs`（不导入对方模型类），不复制评论正文。
    """
    audit = table(
        "audit_logs",
        column("object_id", String(64)),
        column("object_type", String(60)),
        column("action", String(80)),
        column("reason", Text),
        column("created_at", DateTime(timezone=True)),
    )
    rows = await db.execute(
        select(audit.c.object_id, audit.c.reason)
        .where(
            audit.c.object_type == "comment",
            audit.c.action == "comment.hidden",
            audit.c.object_id.in_([str(i) for i in comment_ids]),
        )
        .order_by(audit.c.created_at.desc())
    )
    result: dict[uuid.UUID, str] = {}
    for object_id, reason in rows:
        if not reason:
            continue
        try:
            key = uuid.UUID(str(object_id))
        except (TypeError, ValueError):
            continue
        result.setdefault(key, reason)
    return result


def comment_status(comment: Comment) -> Literal["visible", "withdrawn", "hidden"]:
    if comment.withdrawn_at is not None:
        return "withdrawn"
    return "hidden" if comment.is_hidden else "visible"


async def assert_comment_resource(db: AsyncSession, *, resource_id: uuid.UUID, user) -> None:
    from app.core.enums import Capability
    from app.modules.content.service import assert_visible, get_resource
    from app.modules.access.service import authorize

    resource = await get_resource(db, resource_id)
    await assert_visible(db, resource)
    try:
        await authorize(db, user=user, resource_id=resource_id,
                        version_id=resource.published_version_id, capability=Capability.READ_METADATA)
    except Forbidden as exc:
        raise NotFound("资源不存在") from exc


# =====================================================================
# 纠错（§20.2）
# =====================================================================


def _correction_item(correction: Correction) -> CorrectionItem:
    return CorrectionItem(
        id=correction.id,
        resource_id=correction.resource_id,
        version_id=correction.version_id,
        submitter_id=correction.submitter_id,
        description=correction.description,
        suggested_course_id=correction.suggested_course_id,
        suggested_resource_type=correction.suggested_resource_type,
        status=CorrectionStatus(correction.status),
        resolution_note=correction.resolution_note,
        fixed_version_id=correction.fixed_version_id,
        handled_by=correction.handled_by,
        handled_at=correction.handled_at,
        created_at=correction.created_at,
        updated_at=correction.updated_at,
    )


async def create_correction(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    description: str,
    suggested_course_id: uuid.UUID | None = None,
    suggested_resource_type: ResourceType | None = None,
) -> CorrectionItem:
    """纠错工单绑定版本；新版本修复后由管理员关联工单并通知提交者（§20.2）。"""
    from app.modules.content.service import assert_visible, get_resource, get_version

    resource = await get_resource(db, resource_id)
    await assert_visible(db, resource)
    version = await get_version(db, version_id)
    if version.resource_id != resource_id:
        raise InvalidInput("版本不属于该资源", details={"version_id": str(version_id)})
    from app.modules.identity.service import get_user
    from app.modules.access.service import authorize
    from app.core.enums import Capability

    user = await get_user(db, user_id)
    await authorize(db, user=user, resource_id=resource_id, version_id=version_id,
                    capability=Capability.READ_METADATA)
    if resource.published_version_id != version_id:
        raise InvalidInput("只能对当前公开版本提交纠错")
    if suggested_course_id is not None:
        from app.modules.catalog.service import get_course
        await get_course(db, suggested_course_id)

    correction = Correction(
        resource_id=resource_id,
        version_id=version_id,
        submitter_id=user_id,
        description=description,
        status=CorrectionStatus.OPEN.value,
        suggested_course_id=suggested_course_id,
        suggested_resource_type=suggested_resource_type.value if suggested_resource_type else None,
    )
    db.add(correction)
    await db.flush()
    await emit_event(
        db,
        event_type=DomainEventType.CORRECTION_CREATED,
        subject_type="correction",
        subject_id=correction.id,
        payload={
            "resource_id": str(resource_id),
            "version_id": str(version_id),
            "submitter_id": str(user_id),
        },
        dedupe_key=f"correction.created:{correction.id}",
    )
    return _correction_item(correction)


async def list_my_corrections(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
) -> CursorPage[CorrectionItem]:
    page_size = normalize_page_size(limit)
    stmt = select(Correction).where(Correction.submitter_id == user_id)
    decoded = _decode_time_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                Correction.created_at < created_at,
                (Correction.created_at == created_at) & (Correction.id < last_id),
            )
        )
    stmt = stmt.order_by(Correction.created_at.desc(), Correction.id.desc()).limit(page_size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if has_more and rows
        else None
    )
    return CursorPage(
        items=[_correction_item(c) for c in rows], next_cursor=next_cursor, has_more=has_more
    )


async def list_corrections(
    db: AsyncSession,
    *,
    status: CorrectionStatus | None,
    cursor: str | None,
    limit: int | None,
) -> CursorPage[CorrectionItem]:
    """管理端纠错工单列表；与 list_my_corrections 的区别只是不限定提交人。"""
    page_size = normalize_page_size(limit)
    stmt = select(Correction)
    if status is not None:
        stmt = stmt.where(Correction.status == status.value)
    decoded = _decode_time_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                Correction.created_at < created_at,
                (Correction.created_at == created_at) & (Correction.id < last_id),
            )
        )
    stmt = stmt.order_by(Correction.created_at.desc(), Correction.id.desc()).limit(page_size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if has_more and rows
        else None
    )
    return CursorPage(
        items=[_correction_item(c) for c in rows], next_cursor=next_cursor, has_more=has_more
    )


async def update_correction_status(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    correction_id: uuid.UUID,
    status: CorrectionStatus,
    resolution_note: str | None,
    fixed_version_id: uuid.UUID | None,
    request_id: str,
) -> CorrectionItem:
    """管理员处理纠错工单。主动纠正错误获得正向认可，不惩罚被纠错的贡献者（§20.2、§37）。"""
    correction = (
        await db.execute(select(Correction).where(Correction.id == correction_id).with_for_update())
    ).scalar_one_or_none()
    if correction is None:
        raise NotFound("纠错工单不存在")

    if fixed_version_id is not None:
        from app.modules.content.service import get_version

        version = await get_version(db, fixed_version_id)
        if version.resource_id != correction.resource_id:
            raise InvalidInput("修复版本不属于该资源", details={"fixed_version_id": str(fixed_version_id)})

    previous = CorrectionStatus(correction.status)
    if previous == status:
        return _correction_item(correction)
    correction.status = status.value
    if resolution_note is not None:
        correction.resolution_note = resolution_note
    if fixed_version_id is not None:
        correction.fixed_version_id = fixed_version_id
    correction.handled_by = actor_id
    correction.handled_at = _now()

    await record_audit(
        db,
        action="correction.status",
        object_type="correction",
        object_id=correction.id,
        actor_id=actor_id,
        before={"status": previous.value},
        after={"status": status.value},
        reason=resolution_note,
        request_id=request_id,
    )

    rewardable = (CorrectionStatus.ACCEPTED, CorrectionStatus.FIXED)
    if status in rewardable and previous not in rewardable:
        await _reward_correction(db, correction)
    await emit_event(
        db,
        event_type=DomainEventType.CORRECTION_RESOLVED,
        subject_type="correction",
        subject_id=correction.id,
        payload={"status": status.value, "submitter_id": str(correction.submitter_id), "handled_by": str(actor_id)},
        dedupe_key=f"correction.resolved:{correction.id}:{status.value}",
    )
    await db.flush()
    await db.refresh(correction)
    return _correction_item(correction)


async def apply_classification_fix(db, *, correction_id, actor_id, expected_revision, resolution_note, request_id):
    from app.modules.content.service import correct_classification

    correction = (await db.execute(select(Correction).where(
        Correction.id == correction_id).with_for_update())).scalar_one_or_none()
    if correction is None:
        raise NotFound("纠错工单不存在")
    if correction.status not in ("open", "accepted"):
        raise Conflict("该工单已结案")
    if correction.suggested_course_id is None and correction.suggested_resource_type is None:
        raise InvalidInput("该工单没有分类建议")
    await correct_classification(
        db, resource_id=correction.resource_id, version_id=correction.version_id,
        course_id=correction.suggested_course_id, resource_type=correction.suggested_resource_type,
        expected_revision=expected_revision, actor_id=actor_id, reason=resolution_note,
        request_id=request_id,
    )
    return await update_correction_status(
        db, correction_id=correction_id, actor_id=actor_id, status=CorrectionStatus.FIXED,
        resolution_note=resolution_note, fixed_version_id=None, request_id=request_id,
    )


async def _reward_correction(db: AsyncSession, correction: Correction) -> None:
    """采纳纠错记为正向贡献；从不因"被纠错次数"惩罚原贡献者（§20.2、§37）。"""
    from app.modules.creator.service import record_contribution

    await record_contribution(
        db,
        user_id=correction.submitter_id,
        kind=ContributionKind.ADOPTED_CORRECTION,
        reference_type="correction",
        reference_id=correction.id,
        note="纠错被采纳",
    )


# =====================================================================
# 求资料（§20.2）
# =====================================================================


def _request_dedupe_key(course_id: uuid.UUID, scope: str | None, description: str) -> str:
    raw = "|".join(
        [str(course_id), (scope or "").strip().lower(), _normalize_text(description).lower()]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def _follower_count(db: AsyncSession, request_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count(ResourceRequestFollow.id)).where(
                    ResourceRequestFollow.request_id == request_id
                )
            )
        ).scalar_one()
        or 0
    )


async def _ensure_request_follow(
    db: AsyncSession, *, request_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await db.execute(
        pg_insert(ResourceRequestFollow)
        .values(request_id=request_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=["request_id", "user_id"])
    )


async def request_followers(db, request_id):
    return list((await db.scalars(select(ResourceRequestFollow.user_id).where(ResourceRequestFollow.request_id == request_id))).all())


async def _request_item(
    db: AsyncSession,
    request: ResourceRequest,
    follower_count: int | None = None,
    resolution=None,
    viewer=None,
) -> RequestItem:
    count = follower_count if follower_count is not None else await _follower_count(db, request.id)
    from app.modules.interaction.request_management import resolution_summary
    resource_id, resource_title = (
        resolution
        if resolution is not None
        else await resolution_summary(
            db, request.resolved_resource_id, request.course_id, user=viewer
        )
    )
    return RequestItem(
        id=request.id,
        course_id=request.course_id,
        scope=request.scope,
        description=request.description,
        status=request.status,
        follower_count=count,
        resolved_resource_id=resource_id,
        created_at=request.created_at,
        requester_id=request.requester_id, revision=request.revision, handled_at=request.handled_at,
        resolution_available=resource_id is not None, resolved_resource_title=resource_title,
        resolved_resource_accessible=resource_id is not None,
    )


async def create_request(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    scope: str | None,
    description: str,
) -> RequestCreateResult:
    """提交求资料需求。同课程未解决的同义需求合并关注，避免重复需求无限堆积（§20.2）。"""
    from app.modules.catalog.service import get_course

    await get_course(db, course_id)
    dedupe_key = _request_dedupe_key(course_id, scope, description)

    existing = (
        await db.execute(
            select(ResourceRequest).where(
                ResourceRequest.dedupe_key == dedupe_key,
                ResourceRequest.status == RequestStatus.OPEN.value,
            )
        )
    ).scalars().first()
    if existing is not None:
        await _ensure_request_follow(db, request_id=existing.id, user_id=user_id)
        return RequestCreateResult(created=False, request=await _request_item(db, existing))

    request = ResourceRequest(
        requester_id=user_id,
        course_id=course_id,
        scope=scope,
        description=description,
        status=RequestStatus.OPEN.value,
        dedupe_key=dedupe_key,
    )
    db.add(request)
    await db.flush()
    # 需求提出者自动关注自己的需求，关注人数由此表重算。
    await _ensure_request_follow(db, request_id=request.id, user_id=user_id)
    await emit_event(
        db,
        event_type=DomainEventType.REQUEST_CREATED,
        subject_type="resource_request",
        subject_id=request.id,
        payload={"course_id": str(course_id), "requester_id": str(user_id)},
        dedupe_key=f"request.created:{request.id}",
    )
    return RequestCreateResult(created=True, request=await _request_item(db, request, follower_count=1))


async def list_requests(
    db: AsyncSession,
    *,
    course_id: uuid.UUID | None,
    status: str | None,
    cursor: str | None,
    limit: int | None,
    viewer=None,
    sort: str = "latest",
) -> CursorPage[RequestItem]:
    """需求列表。``sort=latest`` 按创建时间倒序（默认，行为不变）；

    ``sort=hot`` 为资料需求榜：近 7 天创建的开放需求，按关注人数降序 + id 降序，
    游标键与 latest 不同（follower_count/id），两种排序的游标不可混用。
    """
    if sort not in ("latest", "hot"):
        raise InvalidInput("不支持的排序方式", details={"allowed": ["latest", "hot"]})
    page_size = normalize_page_size(limit)
    follower_count = (
        select(func.count(ResourceRequestFollow.id))
        .where(ResourceRequestFollow.request_id == ResourceRequest.id)
        .scalar_subquery()
    )
    stmt = select(ResourceRequest, follower_count.label("follower_count"))
    if course_id is not None:
        stmt = stmt.where(ResourceRequest.course_id == course_id)
    if status is not None:
        try:
            stmt = stmt.where(ResourceRequest.status == RequestStatus(status).value)
        except ValueError as exc:
            raise InvalidInput("需求状态无效", details={"status": status}) from exc
    if sort == "hot":
        # 热点窗口与「开放」约束先于游标过滤，保证翻页边界稳定。
        cutoff = _now() - timedelta(days=REQUEST_HOT_WINDOW_DAYS)
        stmt = stmt.where(
            ResourceRequest.created_at >= cutoff,
            ResourceRequest.status == RequestStatus.OPEN.value,
        )
        decoded = _decode_hot_cursor(cursor)
        if decoded is not None:
            last_followers, last_id = decoded
            stmt = stmt.where(
                or_(
                    follower_count < last_followers,
                    and_(follower_count == last_followers, ResourceRequest.id < last_id),
                )
            )
        stmt = stmt.order_by(follower_count.desc(), ResourceRequest.id.desc())
    else:
        decoded = _decode_time_cursor(cursor)
        if decoded is not None:
            created_at, last_id = decoded
            stmt = stmt.where(
                or_(
                    ResourceRequest.created_at < created_at,
                    (ResourceRequest.created_at == created_at) & (ResourceRequest.id < last_id),
                )
            )
        stmt = stmt.order_by(
            ResourceRequest.created_at.desc(), ResourceRequest.id.desc()
        )
    stmt = stmt.limit(page_size + 1)
    rows = list((await db.execute(stmt)).all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    from app.modules.interaction.request_management import public_summaries
    summaries = await public_summaries(
        db, [(r.resolved_resource_id, r.course_id) for r, _ in rows], user=viewer
    )
    items = [
        await _request_item(db, request, follower_count=int(count or 0), resolution=summaries.get((request.resolved_resource_id, request.course_id), (None, None)))
        for request, count in rows
    ]
    if sort == "hot":
        # 热门游标只带 follower_count 与 id：latest 游标缺 f 键、热门游标缺 t 键，
        # 任一方误用都会在解码处报「cursor 无效」，不会静默串页。
        next_cursor = (
            encode_cursor({"f": int(rows[-1][1] or 0), "i": str(rows[-1][0].id)})
            if has_more and rows
            else None
        )
    else:
        next_cursor = (
            encode_cursor({"t": rows[-1][0].created_at.isoformat(), "i": str(rows[-1][0].id)})
            if has_more and rows
            else None
        )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def list_my_requests(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
    viewer=None,
) -> CursorPage[RequestItem]:
    page_size = normalize_page_size(limit)
    follower_count = (
        select(func.count(ResourceRequestFollow.id))
        .where(ResourceRequestFollow.request_id == ResourceRequest.id)
        .scalar_subquery()
    )
    stmt = select(ResourceRequest, follower_count.label("follower_count")).where(
        ResourceRequest.requester_id == user_id
    )
    decoded = _decode_time_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                ResourceRequest.created_at < created_at,
                (ResourceRequest.created_at == created_at) & (ResourceRequest.id < last_id),
            )
        )
    stmt = stmt.order_by(
        ResourceRequest.created_at.desc(), ResourceRequest.id.desc()
    ).limit(page_size + 1)
    rows = list((await db.execute(stmt)).all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    from app.modules.interaction.request_management import public_summaries
    summaries = await public_summaries(
        db, [(r.resolved_resource_id, r.course_id) for r, _ in rows], user=viewer
    )
    items = [
        await _request_item(db, request, follower_count=int(count or 0), resolution=summaries.get((request.resolved_resource_id, request.course_id), (None, None)))
        for request, count in rows
    ]
    next_cursor = (
        encode_cursor({"t": rows[-1][0].created_at.isoformat(), "i": str(rows[-1][0].id)})
        if has_more and rows
        else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def follow_request(
    db: AsyncSession, *, user_id: uuid.UUID, request_id: uuid.UUID
) -> FollowResult:
    request = (
        await db.execute(select(ResourceRequest).where(ResourceRequest.id == request_id))
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("需求不存在")
    await _ensure_request_follow(db, request_id=request_id, user_id=user_id)
    return FollowResult(followed=True, follower_count=await _follower_count(db, request_id))


# =====================================================================
# 搜索（§19）
# =====================================================================


async def _match_course_ids(db: AsyncSession, query: str) -> list[uuid.UUID]:
    """课程侧匹配由 catalog.search_courses 提供，不在此重复实现课程搜索（§19）。"""
    from app.modules.catalog.service import search_courses

    briefs = await search_courses(db, q=query, limit=50)
    return [brief.course_id for brief in briefs]


async def _course_ids_of_curriculum(db: AsyncSession, curriculum_id: uuid.UUID) -> set[uuid.UUID]:
    from sqlalchemy import Uuid

    rows = await db.execute(
        text("SELECT course_id FROM curriculum_courses WHERE curriculum_id = :cid").bindparams(
            bindparam("cid", type_=Uuid())
        ),
        {"cid": curriculum_id},
    )
    # SQLite 的 uuid 列以无连字符字符串存储，裸 SQL 读回的是 str；统一转回 UUID，
    # 让调用方（含 expanding bindparam 的类型化绑定）拿到一致的对象。
    return {_as_uuid(row[0]) for row in rows}


async def _course_ids_of_university(db: AsyncSession, university_id: uuid.UUID) -> set[uuid.UUID]:
    from sqlalchemy import Uuid

    rows = await db.execute(
        text("SELECT id FROM courses WHERE university_id = :uid").bindparams(
            bindparam("uid", type_=Uuid())
        ),
        {"uid": university_id},
    )
    return {_as_uuid(row[0]) for row in rows}


async def search(
    db: AsyncSession,
    *,
    user=None,
    q: str | None,
    university_id: uuid.UUID | None,
    curriculum_id: uuid.UUID | None,
    course_id: uuid.UUID | None,
    semester_id: uuid.UUID | None,
    teacher_id: uuid.UUID | None,
    resource_type: str | None,
    applicable_year: int | None,
    cursor: str | None,
    limit: int | None,
) -> CursorPage[SearchItem]:
    """§19 搜索与课程发现。

    默认只返回已发布资源；查询失败直接抛出（由错误处理器返回 5xx），
    不吞成空列表，以便前端区分"暂无资料"与"接口故障"（§39 A21）。
    排序先保证相关性，再按课程分组与稳定 ID，避免先按热度跨课程混排（§19.2）。
    """
    page_size = normalize_page_size(limit)
    normalized_q = _normalize_text(q) if q else None
    like_op, year_expr = _like_fragments(db)

    params: dict[str, Any] = {}
    expanding: list[str] = []
    where = ["r.publication_status = 'published'"]

    # 明确课程范围筛选：学校 / 培养方案 / 课程
    explicit_course_ids: set[uuid.UUID] | None = None
    if course_id is not None:
        explicit_course_ids = {course_id}
    if curriculum_id is not None:
        ids = await _course_ids_of_curriculum(db, curriculum_id)
        explicit_course_ids = ids if explicit_course_ids is None else explicit_course_ids & ids
    if university_id is not None:
        ids = await _course_ids_of_university(db, university_id)
        explicit_course_ids = ids if explicit_course_ids is None else explicit_course_ids & ids
    if explicit_course_ids is not None:
        if not explicit_course_ids:
            return CursorPage(items=[], next_cursor=None, has_more=False)
        params["explicit_cids"] = list(explicit_course_ids)
        expanding.append("explicit_cids")
        where.append("r.course_id IN :explicit_cids")

    # 文本相关性：课程匹配（含别名）OR 版本标题 OR 教师公开姓名
    if normalized_q:
        matched_course_ids = await _match_course_ids(db, normalized_q)
        params["title_like"] = f"%{_escape_like(normalized_q)}%"
        clauses = []
        if matched_course_ids:
            params["q_cids"] = matched_course_ids
            expanding.append("q_cids")
            clauses.append("r.course_id IN :q_cids")
        clauses.append(f"rv.title {like_op} :title_like ESCAPE '{_LIKE_ESCAPE}'")
        clauses.append(
            "EXISTS (SELECT 1 FROM offering_teachers ot "
            "JOIN teachers t ON t.id = ot.teacher_id "
            "WHERE ot.offering_id = r.offering_id "
            f"AND t.display_name {like_op} :title_like ESCAPE '{_LIKE_ESCAPE}')"
        )
        where.append("(" + " OR ".join(clauses) + ")")
        rank_expr = (
            f"CASE WHEN rv.title {like_op} :title_like ESCAPE '{_LIKE_ESCAPE}' "
            "THEN 0 ELSE 1 END"
        )
    else:
        rank_expr = "1"

    if resource_type is not None:
        params["resource_type"] = str(resource_type)
        where.append("r.resource_type = :resource_type")

    offering_join = ""
    if semester_id is not None or teacher_id is not None or applicable_year is not None:
        offering_join = "LEFT JOIN course_offerings o ON o.id = r.offering_id"
    if semester_id is not None:
        params["semester_id"] = semester_id
        where.append("o.semester_id = :semester_id")
    if applicable_year is not None:
        params["applicable_year"] = applicable_year
        where.append(
            "o.semester_id IN ("
            f"SELECT id FROM semesters WHERE {year_expr} = :applicable_year)"
        )
    if teacher_id is not None:
        params["teacher_id"] = teacher_id
        where.append(
            "EXISTS (SELECT 1 FROM offering_teachers ot "
            "WHERE ot.offering_id = r.offering_id AND ot.teacher_id = :teacher_id)"
        )

    if cursor:
        last_rank, last_course_id, last_id = _decode_search_cursor(cursor)
        params["cur_rank"] = last_rank
        params["cur_course_id"] = last_course_id
        params["cur_id"] = last_id
        where.append(f"({rank_expr}, r.course_id, r.id) > (:cur_rank, :cur_course_id, :cur_id)")

    sql = f"""
        SELECT r.id, r.course_id, r.resource_type, r.freshness_status,
               r.published_version_id, rv.title, {rank_expr} AS rank
        FROM resources r
        JOIN resource_versions rv ON rv.id = r.published_version_id
        {offering_join}
        WHERE {' AND '.join(where)}
        ORDER BY {rank_expr} ASC, r.course_id ASC, r.id ASC
    """
    from sqlalchemy import Uuid, Integer, String
    from app.modules.access.visibility import visible_resource_ids

    # Filter before LIMIT so hidden resources cannot create holes or leak via pagination.
    # 声明 UUID 参数类型：SQLite 的 DBAPI 不认识 uuid.UUID 对象，Uuid 类型会按方言做
    # 绑定转换（SQLite → CHAR(32) 字符串，PostgreSQL → 原生 uuid），与 ORM 列行为一致。
    uuid_params = [bindparam(name, expanding=True, type_=Uuid()) for name in expanding]
    uuid_params += [
        bindparam(name, type_=Uuid())
        for name in ("semester_id", "teacher_id", "cur_course_id", "cur_id")
        if name in params
    ]
    stmt = text(sql)
    if uuid_params:
        stmt = stmt.bindparams(*uuid_params)
    source = stmt.columns(id=Uuid(), course_id=Uuid(), resource_type=String(),
                          freshness_status=String(), published_version_id=Uuid(),
                          title=String(), rank=Integer()).subquery()
    query = (select(source).where(source.c.id.in_(visible_resource_ids(user)))
             .order_by(source.c.rank, source.c.course_id, source.c.id).limit(page_size + 1))
    rows = list((await db.execute(query, params)).all())

    has_more = len(rows) > page_size
    rows = rows[:page_size]

    resource_ids = [row.id for row in rows]
    summaries = await _rating_summaries(db, resource_ids)
    course_briefs = await _course_brief_map(db, [row.course_id for row in rows])

    items: list[SearchItem] = []
    for row in rows:
        items.append(
            SearchItem(
                resource_id=row.id,
                course_id=row.course_id,
                course=course_briefs.get(row.course_id),
                title=row.title,
                resource_type=row.resource_type,
                freshness_status=row.freshness_status,
                published_version_id=row.published_version_id,
                rating=summaries.get(row.id, _summarize(0, 0.0)),
            )
        )

    next_cursor = (
        encode_cursor(
            {
                "r": int(rows[-1].rank),
                "c": str(rows[-1].course_id),
                "i": str(rows[-1].id),
            }
        )
        if has_more and rows
        else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


async def _course_brief_map(
    db: AsyncSession, course_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    from app.modules.catalog.service import course_brief

    result: dict[uuid.UUID, dict[str, Any]] = {}
    for course_id in dict.fromkeys(course_ids):
        try:
            brief = await course_brief(db, course_id)
        except NotFound:
            continue
        result[course_id] = brief.model_dump() if hasattr(brief, "model_dump") else dict(brief)
    return result


# =====================================================================
# 游标辅助
# =====================================================================


def _decode_time_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    payload = decode_cursor(cursor)
    try:
        created_at = datetime.fromisoformat(str(payload["t"]))
        last_id = uuid.UUID(str(payload["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at, last_id


def _decode_hot_cursor(cursor: str | None) -> tuple[int, uuid.UUID] | None:
    """资料需求榜游标：follower_count + id，与 latest 的 created_at + id 键不同，不可混用。"""
    if not cursor:
        return None
    payload = decode_cursor(cursor)
    try:
        followers = int(payload["f"])
        last_id = uuid.UUID(str(payload["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    return followers, last_id


def _decode_search_cursor(cursor: str) -> tuple[int, uuid.UUID, uuid.UUID]:
    payload = decode_cursor(cursor)
    try:
        rank = int(payload["r"])
        course_id = uuid.UUID(str(payload["c"]))
        last_id = uuid.UUID(str(payload["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    return rank, course_id, last_id
