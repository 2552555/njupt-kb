"""ai 对外 DTO（设计文档 §10、§23、§25.1）。

来源标记：所有生成结果 DTO 都带 `generated=True`、`model` 与 `prompt_template_version`，
前端据此与原文、人工内容区分（§23）。不返回 ORM 对象、密钥或内部提示词全文。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AIProcessKind, AIProcessStatus


# --- 请求 ---
class AIProcessCreateIn(BaseModel):
    """请求某资源版本的某类处理（§23）。"""

    resource_id: uuid.UUID
    version_id: uuid.UUID
    kind: AIProcessKind
    # 未公开内容外发模型需要明确授权；默认不外发（D17、§23）。
    allow_external: bool = False


class QAIn(BaseModel):
    """课程问答。必须指定资源或版本，用于限定检索范围与权限判定（§23、A18）。"""

    question: str = Field(min_length=1, max_length=2000)
    resource_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None


# --- 响应 ---
class AIProcessOut(BaseModel):
    """一次 AI 处理的状态与结果，带明确来源标记。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_id: uuid.UUID
    resource_version_id: uuid.UUID
    kind: AIProcessKind
    status: AIProcessStatus
    requested_by: uuid.UUID | None
    model: str
    prompt_template_version: str
    input_hash: str
    result: dict | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    reuse_of_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # 生成内容标记（§23）：前端据此显示"AI 生成"，不得与原文或人工内容混淆。
    generated: bool = True


class VersionAIOut(BaseModel):
    """某版本已有的生成结果清单（带来源标记）。"""

    resource_id: uuid.UUID
    version_id: uuid.UUID
    items: list[AIProcessOut]


class BudgetPartOut(BaseModel):
    """预算的一段（总预算或单用户预算）。"""

    scope: str
    scope_id: uuid.UUID | None
    period_start: datetime
    period_end: datetime
    limit_cents: int
    used_cents: int
    remaining_cents: int
    configured: bool


class BudgetOut(BaseModel):
    """当前用户与全局的预算使用情况。"""

    global_budget: BudgetPartOut
    user_budget: BudgetPartOut
    generated_at: datetime


class CitationOut(BaseModel):
    """引用来源：只引用用户有权限访问的资源版本（§23）。"""

    resource_id: uuid.UUID
    version_id: uuid.UUID
    title: str | None
    version_no: int | None


class QAOut(BaseModel):
    """课程问答结果。答案只是辅助材料，不是正确答案或授权依据（§35）。"""

    process_id: uuid.UUID
    status: AIProcessStatus
    answer: str
    citations: list[CitationOut]
    generated: bool = True
    model: str
    prompt_template_version: str
    # 成本未知时为 False，前端不得展示编造的金额（§23）。
    cost_known: bool
