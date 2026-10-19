"""cases 模块的对外 DTO（设计文档 §10、§16.2、§17.3）。

只暴露必要字段：不返回 ORM 对象、证据原文、内部备注与处理人身份。
分层返回靠可选字段 + 路由上的 `response_model_exclude_none`：
本人视角把内部字段置 None 即不出现在响应里，处理人视角才填充。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    CaseEventType,
    CaseMeasureKind,
    CaseMeasureStatus,
    CaseStatus,
    CaseSubjectType,
    CaseType,
    RightsNoticeStatus,
)


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- 用户侧：创建案件 ---


class CaseCreateIn(BaseModel):
    case_type: CaseType
    subject_type: CaseSubjectType
    subject_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    rule_version: str | None = Field(default=None, max_length=64)


class CaseListItemOut(_ORMModel):
    id: uuid.UUID
    case_type: CaseType
    subject_type: CaseSubjectType
    subject_id: uuid.UUID
    state: CaseStatus
    title: str
    created_at: datetime
    updated_at: datetime


class CaseEventOut(_ORMModel):
    """案件时间线事件。`note`/`evidence_ref`/`actor_id` 仅在处理人视角填充。"""

    id: uuid.UUID
    event_type: CaseEventType
    served_at: datetime | None = None
    deadline_at: datetime | None = None
    note: str | None = None
    evidence_ref: str | None = None
    actor_id: uuid.UUID | None = None
    created_at: datetime


class CaseDetailOut(_ORMModel):
    id: uuid.UUID
    case_type: CaseType
    subject_type: CaseSubjectType
    subject_id: uuid.UUID
    state: CaseStatus
    title: str
    # 本人提交的理由，本人可见。
    description: str | None = None
    rule_version: str | None = None
    # 处理人身份仅处理人视角返回（§13.2）。
    handler_id: uuid.UUID | None = None
    decided_at: datetime | None = None
    # 对外结论，本人可见。
    decision_reason: str | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    events: list[CaseEventOut] = Field(default_factory=list)


# --- 用户侧：侵权通知与反通知 ---


class RightsNoticeCreateIn(BaseModel):
    claimant_name: str = Field(min_length=1, max_length=300)
    claimant_contact: str = Field(min_length=1, max_length=300)
    rights_basis_ref: str = Field(min_length=1, max_length=512)
    target_type: CaseSubjectType
    target_id: uuid.UUID
    # 具体内容地址／版本说明（不是原件本身）。
    content_ref: str = Field(min_length=1, max_length=1000)
    requested_action: str = Field(min_length=1, max_length=300)
    statement_version: str = Field(min_length=1, max_length=64)
    # 侵权初步证据的引用，不保存证据原文副本。
    evidence_ref: str = Field(min_length=1, max_length=512)
    truthfulness_declared: bool


class RightsNoticeReceiptOut(BaseModel):
    """提交回执：只回显工单号与状态，不回显证据或身份信息。"""

    notice_id: uuid.UUID
    case_id: uuid.UUID
    status: RightsNoticeStatus


class CounterStatementIn(BaseModel):
    statement_text: str = Field(min_length=1, max_length=5000)
    evidence_ref: str | None = Field(default=None, max_length=512)


class CounterStatementOut(_ORMModel):
    id: uuid.UUID
    rights_notice_id: uuid.UUID
    case_id: uuid.UUID
    statement_text: str
    evidence_ref: str | None = None
    created_at: datetime


# --- 后台侧 ---


class CaseAdminOut(_ORMModel):
    id: uuid.UUID
    case_type: CaseType
    subject_type: CaseSubjectType
    subject_id: uuid.UUID
    applicant_id: uuid.UUID
    state: CaseStatus
    rule_version: str | None = None
    handler_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CaseEventIn(BaseModel):
    event_type: CaseEventType
    reason: str = Field(min_length=1, max_length=5000)
    note: str | None = Field(default=None, max_length=5000)
    evidence_ref: str | None = Field(default=None, max_length=512)
    # 转送/送达事实与适用期限（可选，按适用法定路径记录）。
    served_at: datetime | None = None
    deadline_at: datetime | None = None


class MeasureCreateIn(BaseModel):
    measure: CaseMeasureKind
    target_type: CaseSubjectType
    target_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=5000)
    amount_fen: int | None = Field(default=None, ge=0)


class MeasureOut(_ORMModel):
    id: uuid.UUID
    case_id: uuid.UUID
    target_type: CaseSubjectType
    target_id: uuid.UUID
    measure: CaseMeasureKind
    status: CaseMeasureStatus
    amount_fen: int | None = None
    effective_at: datetime | None = None
    released_at: datetime | None = None
    reason: str | None = None
    actor_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    # 商业化关闭时 BLOCK_SALES／FREEZE_REVENUE 只记录措施行，不实际作用资金。
    effect_applied: bool = True
