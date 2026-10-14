"""commerce 模块 DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象、渠道密钥或签名原文（§10）。
金额字段使用 `Decimal`（对应数据库 `Numeric(18, 4)`），序列化为字符串以避免浮点误差。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    Capability,
    LedgerAccount,
    LedgerDirection,
    OrderStatus,
    PaymentAttemptStatus,
    ProductKind,
    RefundStatus,
    ScopeType,
    SettlementStatus,
    WithdrawalStatus,
)


class _InputModel(BaseModel):
    """输入模型基类：拒绝未知字段，避免静默忽略客户端拼写错误。"""

    model_config = ConfigDict(extra="forbid")


# --- 产品与价格（§24.2）---


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    currency: str
    amount: Decimal
    version: int
    is_active: bool


class ProductOut(BaseModel):
    """已上架产品与其生效价格（只读）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ProductKind
    title: str
    description: str | None = None
    owner_id: uuid.UUID | None = None
    grant_scope_type: ScopeType
    grant_scope_id: uuid.UUID | None = None
    grant_capability: Capability
    grant_duration_days: int | None = None
    price: PriceOut | None = None
    created_at: datetime


# --- 订单（§24.2）---


class OrderCreate(_InputModel):
    product_ids: list[uuid.UUID] = Field(min_length=1)
    # 可选：下单同时发起支付；未提供渠道时只创建待支付订单。
    channel: str | None = Field(default=None, max_length=60)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    price_id: uuid.UUID
    title: str
    quantity: int
    unit_amount: Decimal
    amount: Decimal
    currency: str
    grant_scope_type: ScopeType
    grant_scope_id: uuid.UUID | None = None
    grant_capability: Capability
    grant_duration_days: int | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: OrderStatus
    currency: str
    total_amount: Decimal
    expires_at: datetime | None = None
    paid_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    items: list[OrderItemOut] = []


class PaymentAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    channel: str
    status: PaymentAttemptStatus
    amount: Decimal
    currency: str
    channel_txn_id: str | None = None
    channel_reference: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime


# --- 退款（§24.2、§24.3）---


class RefundCreate(_InputModel):
    order_id: uuid.UUID
    # 为空表示整单全额退款。
    amount: Decimal | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=1000)


class RefundConfirmIn(_InputModel):
    channel_refund_id: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    payment_attempt_id: uuid.UUID | None = None
    requested_by: uuid.UUID
    status: RefundStatus
    amount: Decimal
    currency: str
    reason: str
    channel_refund_id: str | None = None
    confirmed_by: uuid.UUID | None = None
    confirmed_at: datetime | None = None
    created_at: datetime


# --- 对账（§24.3）---


class ReconciliationItem(BaseModel):
    """本地订单/支付尝试与渠道状态的差异视图（只读）。"""

    payment_attempt_id: uuid.UUID
    order_id: uuid.UUID
    channel: str
    local_status: PaymentAttemptStatus
    channel_status: str
    amount: Decimal
    currency: str
    channel_amount: Decimal | None = None
    channel_currency: str | None = None
    channel_txn_id: str | None = None
    matches: bool
    note: str | None = None


# --- 账本与提现、结算（§24.3）---


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account: LedgerAccount
    account_id: uuid.UUID | None = None
    direction: LedgerDirection
    amount: Decimal
    currency: str
    business_type: str
    business_id: uuid.UUID | None = None
    entry_group_id: uuid.UUID
    reverses_entry_id: uuid.UUID | None = None
    settlement_id: uuid.UUID | None = None
    memo: str | None = None
    created_at: datetime


class WithdrawalCreate(_InputModel):
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class WithdrawalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    creator_id: uuid.UUID
    status: WithdrawalStatus
    amount: Decimal
    currency: str
    rejection_reason: str | None = None
    processed_at: datetime | None = None
    created_at: datetime


class SettlementRunIn(_InputModel):
    period_start: datetime
    period_end: datetime
    currency: str = Field(min_length=3, max_length=3)


class SettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    creator_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    currency: str
    status: SettlementStatus
    gross_amount: Decimal
    platform_fee: Decimal
    net_amount: Decimal
    rule_ref: str | None = None
    created_at: datetime


class SettlementRunOut(BaseModel):
    """某周期结算结果：created=False 表示该周期已有结算，本次未重复入账。"""

    created: bool
    settlements: list[SettlementOut]
