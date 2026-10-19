"""creator 模块 DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象（§10）。
金额字段使用 `Decimal`（对应 `Numeric(18, 4)`），序列化为字符串以避免浮点误差。
`/me/ledger`、`/me/withdrawals`、`/admin/settlements/run` 的 DTO 是创作者视角的子集，
由 `service` 从 commerce 的 DTO 映射而来。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import (
    ContributionKind,
    ContributorOnboardingStatus,
    LedgerAccount,
    LedgerDirection,
    SettlementStatus,
    WithdrawalStatus,
)


class _InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- 贡献记录（§24.1）---


class ContributionSummary(BaseModel):
    """贡献汇总：按类型计数与展示用贡献分；不承诺兑换现金。"""

    user_id: uuid.UUID
    total: int
    by_kind: dict[str, int]
    points: int
    is_creator: bool


class ContributionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    kind: ContributionKind
    reference_type: str
    reference_id: uuid.UUID
    note: str | None = None
    points: int
    revoked_at: datetime | None = None
    created_at: datetime


class CreatorProfileOut(BaseModel):
    """贡献者公开资料（只读）。贡献者资料与普通账号分离（§24.1）。"""

    user_id: uuid.UUID
    display_name: str
    headline: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    contribution: ContributionSummary


class CreatorSelfProfileOut(CreatorProfileOut):
    """本人视角：额外包含是否公开，且未公开的简介也如实返回，便于回填表单。"""

    is_public: bool = True


class CreatorProfileUpdate(_InputModel):
    """PATCH 语义：字段缺席表示不改，显式 null 表示清空（is_public 除外）。"""

    headline: str | None = Field(default=None, max_length=200)
    bio: str | None = Field(default=None, max_length=2000)
    is_public: bool | None = None

    @field_validator("headline", "bio")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


# --- 贡献者入驻（用户权责 §3.4、§14.1、§16.2）---

# 审核可下达的决定：受理（转审核中）、通过入驻、拒绝。降级/退出本版不实现。
ContributorDecision = Literal["under_review", "active", "rejected"]


class ContributorLifecycleIn(_InputModel):
    expected_status: Literal["active", "suspended"]
    status: Literal["active", "suspended", "exited"]
    reason: str = Field(min_length=1, max_length=255)

    @field_validator("reason")
    @classmethod
    def _required_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("理由不能为空白")
        return value.strip()


class ContributorApplicationCreate(_InputModel):
    """入驻申请：说明能力与权利材料；普通用户登录即可提交。"""

    capability_summary: str = Field(min_length=1, max_length=2000)
    rights_materials_ref: str = Field(min_length=1, max_length=512)
    verification_ref: str | None = Field(default=None, max_length=512)
    contract_party_ref: str | None = Field(default=None, max_length=512)

    @field_validator("capability_summary", "rights_materials_ref")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空白")
        return cleaned


class ContributorApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: ContributorOnboardingStatus
    capability_summary: str
    rights_materials_ref: str
    verification_ref: str | None = None
    contract_party_ref: str | None = None
    submitted_at: datetime
    decided_at: datetime | None = None
    decided_by: uuid.UUID | None = None
    decision_reason: str | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ContributorApplicationDecisionIn(_InputModel):
    """审核决定：理由必填 1–255 字（拒绝需说明理由并允许补充）。"""

    decision: ContributorDecision
    reason: str = Field(min_length=1, max_length=255)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("理由不能为空白")
        return cleaned


# --- 收益相关（P3，经 commerce 提供）---


class CreatorLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account: LedgerAccount
    direction: LedgerDirection
    amount: Decimal
    currency: str
    business_type: str
    business_id: uuid.UUID | None = None
    reverses_entry_id: uuid.UUID | None = None
    memo: str | None = None
    created_at: datetime


class WithdrawalRequest(_InputModel):
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class CreatorWithdrawalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: WithdrawalStatus
    amount: Decimal
    currency: str
    rejection_reason: str | None = None
    processed_at: datetime | None = None
    created_at: datetime


class SettlementRunRequest(_InputModel):
    period_start: datetime
    period_end: datetime
    currency: str = Field(min_length=3, max_length=3)


class CreatorSettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_start: datetime
    period_end: datetime
    currency: str
    status: SettlementStatus
    gross_amount: Decimal
    platform_fee: Decimal
    net_amount: Decimal
    created_at: datetime


class SettlementRunResult(BaseModel):
    """某周期结算结果：created=False 表示该周期已有结算，本次未重复入账。"""

    created: bool
    settlements: list[CreatorSettlementOut]
