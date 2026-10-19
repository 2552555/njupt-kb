"""课程攻略的在线文章模型（ALLIN §20；本次把攻略从「上传文件」升级为可编辑文章）。

设计取舍：
- 文章与资料是两种内容：文章有正文、可在线编辑与按版本审核；资料仍是文件与不可覆盖版本。
- 发布后编辑不覆盖已公开正文：每次提交生成新版本，旧版本继续公开，直到新版本通过审核。
- 审核结论独立于正文版本，审核员只能改状态与备注，不改写作者正文。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Revisioned, Timestamped, UUIDPrimaryKey

__all__ = ["GuideArticle", "GuideArticleVersion", "GuideArticleStatus"]


class GuideArticleStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class GuideArticle(Base, UUIDPrimaryKey, Timestamped, Revisioned):
    """攻略文章主体：指向当前公开版本与最近一次提交。"""

    __tablename__ = "guide_articles"
    __table_args__ = (
        CheckConstraint(_in("status", GuideArticleStatus), name="guide_article_status"),
        Index("ix_guide_articles_course_status", "course_id", "status"),
        Index("ix_guide_articles_author_created", "author_id", "created_at"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 正文存 Markdown 原文；渲染层不执行内联 HTML，避免把作者输入当可执行内容。
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=GuideArticleStatus.DRAFT.value, nullable=False
    )
    # 当前公开版本号；未发布时为空。
    published_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 最近一次提交的版本号（可能是待审或已驳回）。
    latest_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class GuideArticleVersion(Base, UUIDPrimaryKey, Timestamped):
    """文章版本：每次提交审核生成一行，审核结论只作用于该版本。"""

    __tablename__ = "guide_article_versions"
    __table_args__ = (
        UniqueConstraint("article_id", "version_no", name="uq_guide_article_versions_no"),
        CheckConstraint(_in("status", GuideArticleStatus), name="guide_article_version_status"),
        Index("ix_guide_article_versions_status", "status", "submitted_at"),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("guide_articles.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=GuideArticleStatus.PENDING.value, nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
