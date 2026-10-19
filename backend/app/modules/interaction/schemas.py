"""interaction 模块 Pydantic DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象、storage_key 或内部风险参数（§10）。
输入模型禁止未知字段，字段不符返回 422（§25.1）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

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
    body: str | None
    created_at: datetime
    status: Literal["visible", "withdrawn", "hidden"] = "visible"
    revision: int = 1
    # 仅对评论作者（或具备评论管理权限者）返回：管理员隐藏该评论时给出的处理理由。
    moderation_reason: str | None = None


class CommentWithdraw(_InputModel):
    expected_revision: int = Field(ge=1)


class CommentModeration(CommentWithdraw):
    hidden: bool
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写处理理由")
        return value.strip()


# --- 纠错（§20.2）---


class CorrectionCreate(_InputModel):
    resource_id: uuid.UUID
    version_id: uuid.UUID
    description: str = Field(min_length=1, max_length=2000)
    suggested_course_id: uuid.UUID | None = None
    suggested_resource_type: ResourceType | None = None

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


class ClassificationFix(_InputModel):
    expected_revision: int = Field(ge=1)
    resolution_note: str = Field(min_length=1, max_length=2000)


class CorrectionItem(BaseModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    version_id: uuid.UUID
    submitter_id: uuid.UUID
    description: str
    suggested_course_id: uuid.UUID | None = None
    suggested_resource_type: ResourceType | None = None
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
    requester_id: uuid.UUID
    revision: int
    handled_at: datetime | None = None
    resolution_available: bool = False
    resolved_resource_title: str | None = None
    # 解决资源对当前查看者是否可访问（资源不存在/未发布/无可见性/已撤销发布均为 False）。
    # 不可访问时不返回标题等可识别信息，前端据此展示「原解决资料当前不可访问」。
    resolved_resource_accessible: bool = False


class RequestStatusUpdate(_InputModel):
    status: Literal["open", "fulfilled", "closed"]
    expected_revision: int = Field(ge=1)
    resource_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class RequestReopen(_InputModel):
    """复核重开：解决资源失效后由发起者或管理员显式重开，不静默改状态。"""

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("请填写复核重开的理由")
        return cleaned


class RequestResponseCreate(_InputModel):
    resource_id: uuid.UUID


class RequestResponseItem(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    submitted_by: uuid.UUID
    resource_id: uuid.UUID | None
    resource_title: str | None
    available: bool
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


class RecommendationItem(SearchItem):
    reasons: list[str]
    # 是否为固定探索槽位项：前端据此区分展示（其理由中含「新内容，供探索」）。
    explore: bool = False


class RecommendationsOut(BaseModel):
    items: list[RecommendationItem]
