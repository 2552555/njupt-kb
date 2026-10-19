"""课程攻略文章的输入输出 DTO。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GuideArticleCreateIn",
    "GuideArticleListItemOut",
    "GuideArticleOut",
    "GuideArticleUpdateIn",
    "GuidePendingItemOut",
    "GuideReviewIn",
    "GuideWithdrawIn",
]


class GuideArticleCreateIn(BaseModel):
    course_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=500)
    body_markdown: str = Field(min_length=50, max_length=100_000)


class GuideArticleUpdateIn(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=500)
    body_markdown: str | None = Field(default=None, max_length=100_000)


class GuideArticleOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    author_id: uuid.UUID
    title: str
    summary: str | None = None
    body_markdown: str
    status: str
    version_no: int
    source: str
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    revision: int


class GuideArticleListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    author_id: uuid.UUID
    title: str
    summary: str | None = None
    status: str
    published_version_no: int | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GuidePendingItemOut(BaseModel):
    article_id: uuid.UUID
    version_no: int
    title: str
    summary: str | None = None
    author_id: uuid.UUID
    course_id: uuid.UUID
    submitted_at: datetime | None = None


class GuideReviewIn(BaseModel):
    version_no: int = Field(ge=1)
    approve: bool
    note: str | None = Field(default=None, max_length=2000)


class GuideWithdrawIn(BaseModel):
    reason: str = Field(min_length=2, max_length=200)
