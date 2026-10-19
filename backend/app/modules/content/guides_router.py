"""课程攻略文章路由（ALLIN §20）。

只做参数解析与权限依赖；状态机与版本规则在 `guides_service`。
前缀由 `app/main.py` 统一加 `/api/v1`。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_optional_user
from app.core.permissions import RESOURCE_REVIEW, require
from app.modules.content import guides_service
from app.modules.content.guides_schemas import (
    GuideArticleCreateIn,
    GuideArticleListItemOut,
    GuideArticleOut,
    GuideArticleUpdateIn,
    GuidePendingItemOut,
    GuideReviewIn,
    GuideWithdrawIn,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["guides"])


@router.post("/guides", response_model=GuideArticleOut, status_code=status.HTTP_201_CREATED)
async def create_guide(
    payload: GuideArticleCreateIn,
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> GuideArticleOut:
    article = await guides_service.create_article(
        db,
        author=user,
        course_id=payload.course_id,
        title=payload.title,
        summary=payload.summary,
        body_markdown=payload.body_markdown,
    )
    data = await guides_service.get_article(db, article_id=article.id, viewer=user)
    return GuideArticleOut(**data)


@router.get("/guides")
async def list_guides(
    course_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    page = await guides_service.list_articles(
        db, course_id=course_id, cursor=cursor, limit=limit
    )
    return {
        "items": [GuideArticleListItemOut.model_validate(row) for row in page["items"]],
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
    }


@router.get("/guides/{article_id}", response_model=GuideArticleOut)
async def get_guide(
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    viewer: "User | None" = Depends(get_optional_user),
) -> GuideArticleOut:
    data = await guides_service.get_article(db, article_id=article_id, viewer=viewer)
    return GuideArticleOut(**data)


@router.patch("/guides/{article_id}", response_model=GuideArticleOut)
async def update_guide(
    article_id: uuid.UUID,
    payload: GuideArticleUpdateIn,
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> GuideArticleOut:
    await guides_service.update_article(
        db,
        actor=user,
        article_id=article_id,
        expected_revision=payload.expected_revision,
        title=payload.title,
        summary=payload.summary,
        body_markdown=payload.body_markdown,
    )
    data = await guides_service.get_article(db, article_id=article_id, viewer=user)
    return GuideArticleOut(**data)


@router.post("/guides/{article_id}/submit", response_model=GuideArticleOut)
async def submit_guide(
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> GuideArticleOut:
    await guides_service.submit_article(db, actor=user, article_id=article_id)
    data = await guides_service.get_article(db, article_id=article_id, viewer=user)
    return GuideArticleOut(**data)


@router.post("/guides/{article_id}/withdraw", response_model=GuideArticleOut)
async def withdraw_guide(
    article_id: uuid.UUID,
    payload: GuideWithdrawIn,
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> GuideArticleOut:
    await guides_service.withdraw_article(
        db, actor=user, article_id=article_id, reason=payload.reason
    )
    data = await guides_service.get_article(db, article_id=article_id, viewer=user)
    return GuideArticleOut(**data)


@router.get("/me/guides")
async def list_my_guides(
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> dict[str, object]:
    page = await guides_service.list_my_articles(
        db, author_id=user.id, cursor=cursor, limit=limit
    )
    return {
        "items": [GuideArticleListItemOut.model_validate(row) for row in page["items"]],
        "has_more": page["has_more"],
        "total": page["total"],
    }


@router.get("/admin/guides/pending")
async def list_pending_guides(
    limit: int | None = Query(default=None, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(RESOURCE_REVIEW)),
) -> dict[str, object]:
    page = await guides_service.list_pending(db, limit=limit)
    return {
        "items": [GuidePendingItemOut(**row) for row in page["items"]],
        "total": page["total"],
    }


@router.post("/admin/guides/{article_id}/decisions", response_model=GuideArticleOut)
async def review_guide(
    article_id: uuid.UUID,
    payload: GuideReviewIn,
    db: AsyncSession = Depends(get_db),
    reviewer: "User" = Depends(require(RESOURCE_REVIEW)),
) -> GuideArticleOut:
    await guides_service.review_article(
        db,
        reviewer=reviewer,
        article_id=article_id,
        version_no=payload.version_no,
        approve=payload.approve,
        note=payload.note,
    )
    data = await guides_service.get_article(db, article_id=article_id, viewer=reviewer)
    return GuideArticleOut(**data)
