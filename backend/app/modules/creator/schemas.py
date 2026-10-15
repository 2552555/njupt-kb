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

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    ContributionKind,
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
