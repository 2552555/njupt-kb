"""interaction 模块表定义（设计文档 §19、§20）。

收藏、评价、关注、评论、纠错与求资料分别建模，不用一张"点赞表"承载所有反馈（§20.2）。
跨模块外键只引用表名字符串，不导入对方模型类（docs/ARCHITECTURE.md §2）。
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

from app.core.enums import CorrectionStatus, RequestStatus
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "FollowTargetType",
    "Favorite",
    "Rating",
    "Follow",
    "Comment",
    "Correction",
    "ResourceRequest",
    "ResourceRequestFollow",
]


class FollowTargetType(StrEnum):
    """§20.2 关注对象为课程或创作者，与收藏资料分开建模。"""

    COURSE = "course"
    CREATOR = "creator"


class Favorite(Base, UUIDPrimaryKey, Timestamped):
    """§20.1 收藏作用于资源稳定 ID，公开版本变更后仍保留（§15.2 唯一约束）。"""

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "resource_id", name="uq_favorites_user_resource"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Rating(Base, UUIDPrimaryKey, Timestamped):
    """§20.1 评价关联实际使用版本；一个用户对同一版本只有一条有效评分。

    聚合结果由本表可重算（§15.2），不维护无法重算的冗余列。
    resource_id 冗余保存，便于按资源聚合而不跨模块联表。
    """

    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "version_id", name="uq_ratings_user_version"),
        CheckConstraint("score >= 1 AND score <= 5", name="score_range"),
        Index("ix_ratings_resource_id", "resource_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)


class Follow(Base, UUIDPrimaryKey, Timestamped):
    """§20.2 关注课程或创作者，用 target_type + target_id 多态表达。"""

    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_follows_user_target"),
        CheckConstraint("target_type IN ('course', 'creator')", name="target_type"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 多态目标无法用单一外键表达，故只保存 UUID，由服务层校验目标存在。
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


class Comment(Base, UUIDPrimaryKey, Timestamped):
    """§20.2 评论与回复。内容按纯文本处理，不作为任意 HTML 执行（§28）。"""

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_resource_created", "resource_id", "created_at"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Correction(Base, UUIDPrimaryKey, Timestamped):
    """§20.2 结构化纠错工单，绑定版本，可标记 accepted / rejected / fixed。"""

    __tablename__ = "corrections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'accepted', 'rejected', 'fixed')", name="status"
        ),
        Index("ix_corrections_resource_id", "resource_id"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    submitter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CorrectionStatus.OPEN.value
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 新版本修复后关联工单，用于通知提交者（§20.2）。
    fixed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resource_versions.id", ondelete="SET NULL"), nullable=True
    )
    handled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceRequest(Base, UUIDPrimaryKey, Timestamped):
    """§20.2 求资料：课程、范围、描述、解决资源；dedupe_key 抑制重复需求堆积。"""

    __tablename__ = "resource_requests"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'fulfilled', 'closed')", name="status"),
        Index("ix_resource_requests_dedupe", "dedupe_key"),
    )

    requester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RequestStatus.OPEN.value
    )
    resolved_resource_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class ResourceRequestFollow(Base, UUIDPrimaryKey, Timestamped):
    """§20.2 需求关注人数的事实来源，可从本表重算。"""

    __tablename__ = "resource_request_follows"
    __table_args__ = (
        UniqueConstraint("request_id", "user_id", name="uq_request_follows_request_user"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
