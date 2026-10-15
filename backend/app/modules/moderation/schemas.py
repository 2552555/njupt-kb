"""moderation 模块的对外 DTO（设计文档 §10、§25.1）。

只暴露必要字段：不返回 ORM 对象、存储路径、内部风险参数。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    FreshnessStatus,
    ModerationDecisionType,
    ReportStatus,
    ReviewStatus,
)

# §18 投诉理由分类。不受约束的自由文本不做工单键，统一用有限分类 + 描述。
ReportReasonCode = Literal[
    "copyright",
    "privacy",
    "inappropriate",
    "misinformation",
    "spam",
    "malware",
    "other",
]

# 发布动作：授权发布、恢复或回滚。回滚是新发布动作，不删历史（§12.1）。
PublicationAction = Literal["publish", "restore", "rollback"]

# 投诉处置中的临时措施：与最终认定分开记录（§18）。
InterimAction = Literal["none", "restrict_access", "release_access"]


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- 审核队列与决定 ---


class ReviewOut(_ORMModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    version_id: uuid.UUID
    submitter_id: uuid.UUID
    status: ReviewStatus
    snapshot_revision: int
    assigned_reviewer_id: uuid.UUID | None = None
    submitted_at: datetime
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # 当前请求者是否可对该任务下决定（作者不能审核自己的提交，§13.3）。
    can_decide: bool | None = None


class ReviewDecisionIn(BaseModel):
    decision: ModerationDecisionType
    # 审核员必须给出明确结论和理由（§13.2）。
    reason: str = Field(min_length=1, max_length=2000)
    # 审核员所查看快照的标识；与当前快照不一致时返回冲突（§13.3）。
    snapshot_revision: int = Field(ge=1)


class ModerationDecisionOut(_ORMModel):
    id: uuid.UUID
    review_id: uuid.UUID
    version_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: ModerationDecisionType
    reason: str
    snapshot_revision: int
    created_at: datetime


# --- 发布与时效 ---


class PublicationIn(BaseModel):
    action: PublicationAction
    # publish / rollback 必填；restore 使用资源当前公开版本。
    version_id: uuid.UUID | None = None
    # 条件更新的期望修订号：过期页面提交返回 409，不能覆盖新结果（§13.3、§29）。
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class FreshnessIn(BaseModel):
    status: FreshnessStatus
    reason: str | None = Field(default=None, max_length=1000)


class PublicationOut(BaseModel):
    """发布结果：记录生效的目标版本与动作。"""

    resource_id: uuid.UUID
    version_id: uuid.UUID
    action: PublicationAction
    publication_status: Literal["published"] = "published"


# --- 举报与工单 ---


class ReportCreateIn(BaseModel):
    resource_id: uuid.UUID
    version_id: uuid.UUID | None = None
    reason_code: ReportReasonCode
    description: str | None = Field(default=None, max_length=2000)


class ReportOut(_ORMModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    version_id: uuid.UUID | None = None
    reporter_id: uuid.UUID
    reason_code: str
    description: str | None = None
    status: ReportStatus
    created_at: datetime
    updated_at: datetime


class ReportEventIn(BaseModel):
    # open → triaged → investigating → resolved / dismissed → appealed（§18）。
    to_status: ReportStatus
    note: str | None = Field(default=None, max_length=2000)
    evidence: dict[str, Any] | None = None
    # 高风险内容可先限制访问，但必须与最终认定区分（§18）。
    interim_action: InterimAction = "none"


class ReportEventOut(_ORMModel):
    id: uuid.UUID
    report_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    from_status: ReportStatus | None = None
    to_status: ReportStatus
    note: str | None = None
    evidence: dict[str, Any] | None = None
    created_at: datetime


# --- 审计 ---


class AuditLogOut(_ORMModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None = None
    action: str
    object_type: str
    object_id: str | None = None
    object_version: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    reason: str | None = None
    request_id: str | None = None
    created_at: datetime


# --- 后台概览 ---


class ModerationSummaryOut(BaseModel):
    reviews: dict[str, int]
    reports: dict[str, int]
    oldest_pending_submitted_at: datetime | None = None
    generated_at: datetime


# --- 目录维护与账号限制 ---


class CatalogCorrectionIn(BaseModel):
    """课程归属更正：真正目录数据由 catalog 模块维护（§11.2、§12.1）。"""

    course_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class UserSuspendIn(BaseModel):
    """暂停/封禁有原因、范围、起止时间（§14.1）。"""

    reason: str = Field(min_length=1, max_length=1000)
    until: datetime | None = None


class UserReinstateIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
