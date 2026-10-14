"""interaction 模块 Pydantic DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象、storage_key 或内部风险参数（§10）。
输入模型禁止未知字段，字段不符返回 422（§25.1）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import CorrectionStatus, ResourceType
from app.modules.interaction.models import FollowTargetType


class _InputModel(BaseModel):
    """输入模型基类：拒绝未知字段，避免静默忽略客户端拼写错误。"""

    model_config = ConfigDict(extra="forbid")


# --- 评价（§20.1）---


class RatingSummary(BaseModel):
    """§20.1 评分聚合：均值、评价人数与收缩后评分。

    该 DTO 即 docs/INTERFACES.md 中 interaction.resource_rating_summary 的返回类型。
    """

    average: float
    count: int
    shrunk: float


class RatingSubmit(_InputModel):
    score: int = Field(ge=1, le=5, description="1-5 星")


# --- 收藏（§20.1）---


class FavoriteItem(BaseModel):
    resource_id: uuid.UUID
    created_at: datetime
    # 资源下架时显示不可用状态，不自动打开旧版文件（§20.1）。
    available: bool
    publication_status: str
    course_id: uuid.UUID | None = None
    title: str | None = None


# --- 关注（§20.2）---


class FollowItem(BaseModel):
    id: uuid.UUID
    target_type: FollowTargetType
    target_id: uuid.UUID
    created_at: datetime
    # 目标摘要：课程为 CourseBrief，创作者为公开昵称；目标缺失时为 None。
    target: dict[str, Any] | None = None


# --- 评论（§20.2）---


class CommentCreate(_InputModel):
    body: str = Field(min_length=1, max_length=2000)
    parent_id: uuid.UUID | None = None

    @field_validator("body")
    @classmethod
    def _strip_body(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("评论内容不能为空")
        return cleaned


class CommentItem(BaseModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    parent_id: uuid.UUID | None = None
    body: str
    created_at: datetime


# --- 纠错（§20.2）---


class CorrectionCreate(_InputModel):
    resource_id: uuid.UUID
    version_id: uuid.UUID
    description: str = Field(min_length=1, max_length=2000)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("纠错说明不能为空")
        return cleaned


class CorrectionStatusUpdate(_InputModel):
    status: CorrectionStatus
    resolution_note: str | None = Field(default=None, max_length=2000)
    fixed_version_id: uuid.UUID | None = None


class CorrectionItem(BaseModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    version_id: uuid.UUID
    submitter_id: uuid.UUID
    description: str
    status: CorrectionStatus
    resolution_note: str | None = None
    fixed_version_id: uuid.UUID | None = None
    handled_by: uuid.UUID | None = None
    handled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# --- 求资料（§20.2）---


class RequestCreate(_InputModel):
    course_id: uuid.UUID
    scope: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=2000)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("需求描述不能为空")
        return cleaned


class RequestItem(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    scope: str | None = None
    description: str
    status: str
    follower_count: int
    resolved_resource_id: uuid.UUID | None = None
    created_at: datetime


class RequestCreateResult(BaseModel):
    """created=False 表示命中同课程的未解决需求，已合并关注而非重复建单。"""

    created: bool
    request: RequestItem


class FollowResult(BaseModel):
    followed: bool
    follower_count: int


# --- 搜索（§19）---


class SearchItem(BaseModel):
    """搜索结果返回公开的适用信息与版本摘要（§19.2）。"""

    resource_id: uuid.UUID
    course_id: uuid.UUID
    # 课程摘要帮助用户区分同名不同课程代码的结果（§19.2）。
    course: dict[str, Any] | None = None
    title: str
    resource_type: ResourceType | None = None
    freshness_status: str | None = None
    published_version_id: uuid.UUID | None = None
    rating: RatingSummary
