"""课程攻略文章的编辑、提交与审核规则（ALLIN §20）。

状态机（与资料版本语义一致，但不共用文件链路）：

    draft --submit--> pending --approve--> published
                          |                    |
                          +--reject--> rejected  （已有公开版本时旧版继续公开）

- 发布后编辑不覆盖已公开正文：每次提交生成新版本，审核通过才切换公开版本。
- 审核员只改状态与备注，不改写正文；审核结论绑定具体版本号。
- 公开列表只返回 `published`，正文取 `published_version_no` 对应版本；
  作者可看自己的最新版本，审核员可看待审版本。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationType, Role
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound, RateLimited
from app.core.pagination import decode_cursor, encode_cursor, normalize_page_size
from app.core.ratelimit import check_rate_limit
from app.modules.content.guides_models import (
    GuideArticle,
    GuideArticleStatus,
    GuideArticleVersion,
)

__all__ = [
    "create_article",
    "get_article",
    "list_articles",
    "list_my_articles",
    "list_pending",
    "review_article",
    "submit_article",
    "update_article",
    "withdraw_article",
]

MAX_TITLE = 200
MAX_SUMMARY = 500
MAX_BODY_CHARS = 100_000
MIN_BODY_CHARS = 50
CREATE_PER_HOUR = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clean_text(value: str | None, *, field: str, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) > limit:
        raise InvalidInput(f"{field}不能超过 {limit} 个字符")
    return text or None


def _validate_body(body: str) -> str:
    text = (body or "").strip()
    if len(text) < MIN_BODY_CHARS:
        raise InvalidInput(f"正文至少 {MIN_BODY_CHARS} 个字符")
    if len(text) > MAX_BODY_CHARS:
        raise InvalidInput(f"正文不能超过 {MAX_BODY_CHARS} 个字符")
    return text


async def _ensure_course(db: AsyncSession, course_id: uuid.UUID) -> None:
    from app.modules.catalog.models import Course

    exists = (
        await db.execute(select(Course.id).where(Course.id == course_id))
    ).scalar_one_or_none()
    if exists is None:
        raise NotFound("课程不存在")


async def create_article(
    db: AsyncSession,
    *,
    author: Any,
    course_id: uuid.UUID,
    title: str,
    summary: str | None,
    body_markdown: str,
) -> GuideArticle:
    """创建草稿。草稿只有作者可见，不进入公开列表与审核队列。"""
    allowed = await check_rate_limit(
        f"guide-create:{author.id}", limit=CREATE_PER_HOUR, window_seconds=3600
    )
    if not allowed:
        raise RateLimited("创建攻略过于频繁，请稍后再试", details={"retry_after": 3600})

    await _ensure_course(db, course_id)
    clean_title = _clean_text(title, field="标题", limit=MAX_TITLE)
    if not clean_title:
        raise InvalidInput("标题不能为空")
    article = GuideArticle(
        course_id=course_id,
        author_id=author.id,
        title=clean_title,
        summary=_clean_text(summary, field="摘要", limit=MAX_SUMMARY),
        body_markdown=_validate_body(body_markdown),
        status=GuideArticleStatus.DRAFT.value,
        latest_version_no=0,
        revision=1,
    )
    db.add(article)
    await db.flush()
    return article


async def _load_article(db: AsyncSession, article_id: uuid.UUID) -> GuideArticle:
    article = (
        await db.execute(select(GuideArticle).where(GuideArticle.id == article_id))
    ).scalar_one_or_none()
    if article is None:
        raise NotFound("攻略不存在")
    return article


def _can_edit(article: GuideArticle, actor: Any) -> bool:
    return article.author_id == actor.id or getattr(actor, "role", None) == Role.ADMIN


async def update_article(
    db: AsyncSession,
    *,
    actor: Any,
    article_id: uuid.UUID,
    expected_revision: int,
    title: str | None,
    summary: str | None,
    body_markdown: str | None,
) -> GuideArticle:
    """编辑草稿/被驳回的攻略；并发编辑用 revision 条件更新，过期返回冲突。"""
    article = await _load_article(db, article_id)
    if not _can_edit(article, actor):
        raise Forbidden("只能编辑自己的攻略")
    if article.status == GuideArticleStatus.PENDING.value:
        raise Conflict("攻略正在审核中，不能编辑；请等待审核结论")
    if article.status == GuideArticleStatus.WITHDRAWN.value:
        raise Conflict("攻略已撤回，不能继续编辑")
    if article.revision != expected_revision:
        raise Conflict("攻略已被其他操作更新，请刷新后重试")

    if title is not None:
        clean_title = _clean_text(title, field="标题", limit=MAX_TITLE)
        if not clean_title:
            raise InvalidInput("标题不能为空")
        article.title = clean_title
    if summary is not None:
        article.summary = _clean_text(summary, field="摘要", limit=MAX_SUMMARY)
    if body_markdown is not None:
        article.body_markdown = _validate_body(body_markdown)
    article.revision += 1
    await db.flush()
    return article


async def submit_article(
    db: AsyncSession, *, actor: Any, article_id: uuid.UUID
) -> GuideArticleVersion:
    """提交审核：生成新版本（含当前正文快照），旧公开版本继续可见。"""
    article = await _load_article(db, article_id)
    if not _can_edit(article, actor):
        raise Forbidden("只能提交自己的攻略")
    if article.status == GuideArticleStatus.PENDING.value:
        raise Conflict("攻略已在审核队列中")
    if article.status == GuideArticleStatus.WITHDRAWN.value:
        raise Conflict("攻略已撤回，不能提交")

    next_no = int(article.latest_version_no or 0) + 1
    version = GuideArticleVersion(
        article_id=article.id,
        version_no=next_no,
        title=article.title,
        summary=article.summary,
        body_markdown=article.body_markdown,
        status=GuideArticleStatus.PENDING.value,
        submitted_at=_now(),
    )
    db.add(version)
    article.latest_version_no = next_no
    article.status = GuideArticleStatus.PENDING.value
    article.revision += 1
    await db.flush()

    # 通知审核队列负责人由管理端轮询；这里只给作者一条「已收到」记录。
    from app.modules.notification.service import notify

    await notify(
        db,
        user_id=article.author_id,
        type=NotificationType.SUBMISSION_RECEIVED,
        title="课程攻略已提交审核",
        body=f"《{article.title}》第 {next_no} 版已进入审核队列。",
        data={"article_id": str(article.id), "version_no": next_no},
    )
    return version


async def review_article(
    db: AsyncSession,
    *,
    reviewer: Any,
    article_id: uuid.UUID,
    version_no: int,
    approve: bool,
    note: str | None,
) -> GuideArticle:
    """审核指定版本；通过才切换公开版本，驳回保留旧公开版本（若有）。"""
    article = await _load_article(db, article_id)
    version = (
        await db.execute(
            select(GuideArticleVersion).where(
                GuideArticleVersion.article_id == article_id,
                GuideArticleVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFound("该版本的攻略不存在")
    if version.status != GuideArticleStatus.PENDING.value:
        raise Conflict("该版本已有审核结论，不能重复处理")

    clean_note = _clean_text(note, field="审核备注", limit=2000)
    version.reviewed_at = _now()
    version.reviewer_id = getattr(reviewer, "id", None)
    version.review_note = clean_note

    if approve:
        version.status = GuideArticleStatus.PUBLISHED.value
        article.status = GuideArticleStatus.PUBLISHED.value
        article.published_version_no = version.version_no
        article.published_at = version.reviewed_at
        # 主体正文同步为已发布内容，便于列表/搜索直接读取。
        article.title = version.title
        article.summary = version.summary
        article.body_markdown = version.body_markdown
    else:
        version.status = GuideArticleStatus.REJECTED.value
        # 有已公开版本时保持 published：驳回新版本不影响读者已看到的内容。
        article.status = (
            GuideArticleStatus.PUBLISHED.value
            if article.published_version_no is not None
            else GuideArticleStatus.REJECTED.value
        )

    article.revision += 1
    await db.flush()

    from app.modules.notification.service import notify

    await notify(
        db,
        user_id=article.author_id,
        type=NotificationType.REVIEW_RESULT,
        title="课程攻略审核通过" if approve else "课程攻略需要修改",
        body=(
            f"《{article.title}》第 {version_no} 版已通过审核。"
            if approve
            else f"《{article.title}》第 {version_no} 版未通过审核，请按备注修改后重新提交。"
        ),
        data={
            "article_id": str(article.id),
            "version_no": version_no,
            "approved": approve,
        },
    )
    return article


async def withdraw_article(
    db: AsyncSession, *, actor: Any, article_id: uuid.UUID, reason: str
) -> GuideArticle:
    """作者撤回：停止公开展示，保留历史版本与审核记录。"""
    article = await _load_article(db, article_id)
    if not _can_edit(article, actor):
        raise Forbidden("只能撤回自己的攻略")
    if article.status == GuideArticleStatus.WITHDRAWN.value:
        return article
    article.status = GuideArticleStatus.WITHDRAWN.value
    article.published_version_no = None
    article.revision += 1
    await db.flush()
    return article


async def get_article(
    db: AsyncSession, *, article_id: uuid.UUID, viewer: Any | None
) -> dict[str, Any]:
    """详情：公开取已发布版本；作者取最新版本；审核员可看待审版本。"""
    article = await _load_article(db, article_id)
    is_author = viewer is not None and article.author_id == getattr(viewer, "id", None)
    is_admin = viewer is not None and getattr(viewer, "role", None) in {
        Role.ADMIN,
        Role.REVIEWER,
    }

    # 有已发布版本时，公开读者始终看已发布内容——即使新版本正在待审或被驳回。
    if article.published_version_no:
        version_no = article.published_version_no
        source = "published"
    elif is_author or is_admin:
        version_no = int(article.latest_version_no or 0)
        source = "latest"
    else:
        raise NotFound("攻略不存在或尚未公开")

    if version_no <= 0:
        # 只有草稿、从未提交：作者可见自己的草稿正文。
        return {
            "id": article.id,
            "course_id": article.course_id,
            "author_id": article.author_id,
            "title": article.title,
            "summary": article.summary,
            "body_markdown": article.body_markdown,
            "status": article.status,
            "version_no": 0,
            "source": "draft",
            "published_at": article.published_at,
            "created_at": article.created_at,
            "updated_at": article.updated_at,
            "revision": article.revision,
        }

    version = (
        await db.execute(
            select(GuideArticleVersion).where(
                GuideArticleVersion.article_id == article.id,
                GuideArticleVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFound("攻略版本不存在")
    if version.status not in (
        GuideArticleStatus.PUBLISHED.value,
        GuideArticleStatus.PENDING.value,
        GuideArticleStatus.REJECTED.value,
    ):
        raise NotFound("攻略不存在或尚未公开")

    return {
        "id": article.id,
        "course_id": article.course_id,
        "author_id": article.author_id,
        "title": version.title,
        "summary": version.summary,
        "body_markdown": version.body_markdown,
        "status": version.status,
        "version_no": version.version_no,
        "source": source,
        "published_at": article.published_at,
        "created_at": article.created_at,
        "updated_at": article.updated_at,
        "revision": article.revision,
    }


def _offset_of(cursor: str | None) -> int:
    if not cursor:
        return 0
    payload = decode_cursor(cursor)
    value = payload.get("o")
    if not isinstance(value, int) or value < 0:
        raise InvalidInput("cursor 无效")
    return value


async def list_articles(
    db: AsyncSession, *, course_id: uuid.UUID, cursor: str | None, limit: int | None
) -> dict[str, Any]:
    """公开列表：只返回已发布攻略，按发布时间倒序。

    用 offset 游标而不是时间戳游标：公开列表规模有限，避免 SQLite/PostgreSQL
    对带时区时间比较的差异，也避免同一时间戳多条记录翻页丢失。
    """
    size = normalize_page_size(limit)
    offset = _offset_of(cursor)
    stmt = (
        select(GuideArticle)
        .where(
            GuideArticle.course_id == course_id,
            GuideArticle.status == GuideArticleStatus.PUBLISHED.value,
        )
        .order_by(GuideArticle.published_at.desc(), GuideArticle.id.desc())
        .offset(offset)
        .limit(size + 1)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = encode_cursor({"o": offset + size}) if has_more else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


async def list_my_articles(
    db: AsyncSession, *, author_id: uuid.UUID, cursor: str | None, limit: int | None
) -> dict[str, Any]:
    size = normalize_page_size(limit)
    stmt = (
        select(GuideArticle)
        .where(GuideArticle.author_id == author_id)
        .order_by(GuideArticle.updated_at.desc(), GuideArticle.id.desc())
        .limit(size + 1)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    return {
        "items": items,
        "next_cursor": None,
        "has_more": has_more,
        "total": int(
            (
                await db.execute(
                    select(sa_func.count(GuideArticle.id)).where(
                        GuideArticle.author_id == author_id
                    )
                )
            ).scalar_one()
        ),
    }


async def list_pending(
    db: AsyncSession, *, limit: int | None = None
) -> dict[str, Any]:
    """审核队列：待审版本 + 文章标题与作者，按提交时间升序（先到先审）。"""
    size = normalize_page_size(limit)
    rows = (
        await db.execute(
            select(GuideArticleVersion, GuideArticle)
            .join(GuideArticle, GuideArticle.id == GuideArticleVersion.article_id)
            .where(GuideArticleVersion.status == GuideArticleStatus.PENDING.value)
            .order_by(GuideArticleVersion.submitted_at.asc())
            .limit(size)
        )
    ).all()
    return {
        "items": [
            {
                "article_id": article.id,
                "version_no": version.version_no,
                "title": version.title,
                "summary": version.summary,
                "author_id": article.author_id,
                "course_id": article.course_id,
                "submitted_at": version.submitted_at,
            }
            for version, article in rows
        ],
        "total": len(rows),
    }
