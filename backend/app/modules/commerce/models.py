"""commerce 表定义（设计文档 §24.2、§24.3、§10）。

订单、支付、退款、权益、账本与结算分开建模；金额一律 `Numeric(18, 4)` 定点 + 币种字段，
禁止浮点（§10）。

跨模块外键只引用字符串表名（docs/ARCHITECTURE.md §2.2），不导入其他模块的模型类：
- `users`（identity）
- 权益表 `entitlements` 由 access 拥有，本模块只在服务层按需局部导入其模型类写入权益。

追加写入表 `ledger_entries` 只有 `created_at`：已入账金额通过反向分录更正，
不直接修改历史记录（§24.3）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    Capability,
    LedgerAccount,
    LedgerDirection,
    OrderStatus,
    PaymentAttemptStatus,
    ProductKind,
    PublicationStatus,
    RefundStatus,
    ScopeType,
    SettlementStatus,
    WithdrawalStatus,
)
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "Product",
    "Price",
    "Order",
    "OrderItem",
    "PaymentAttempt",
    "Refund",
    "Settlement",
    "LedgerEntry",
    "Withdrawal",
]

# 定点金额：18 位有效数字、4 位小数，禁用浮点（§10）。
MONEY = Numeric(18, 4)


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """由枚举生成 CheckConstraint 表达式，取值以 app.core.enums 为唯一来源。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class Product(Base, UUIDPrimaryKey, Timestamped):
    """购买对象（§24.2）：会员、课程包或单资源权益，并声明购买后授予的权益模板。"""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(_in("kind", ProductKind), name="kind_valid"),
        CheckConstraint(_in("publication_status", PublicationStatus), name="publication_status_valid"),
        CheckConstraint(_in("grant_scope_type", ScopeType), name="grant_scope_type_valid"),
        CheckConstraint(_in("grant_capability", Capability), name="grant_capability_valid"),
        CheckConstraint(
            "grant_duration_days IS NULL OR grant_duration_days > 0",
            name="grant_duration_positive",
        ),
        Index("ix_products_status_kind", "publication_status", "kind"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 归属创作者；为空表示平台自营产品，结算时不产生创作者收益。
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # 产品上架状态；只有 published 才出现在 GET /products。
    publication_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublicationStatus.UNPUBLISHED.value, index=True
    )
    # 购买后授予权益的模板（scope_type + scope_id + capability）。
    grant_scope_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScopeType.EXPLICIT_GRANT.value
    )
    grant_scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    grant_capability: Mapped[str] = mapped_column(
        String(32), nullable=False, default=Capability.READ_FULL.value
    )
    # 权益有效期天数；为空表示不设到期时间。
    grant_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Price(Base, UUIDPrimaryKey, Timestamped):
    """计价版本与币种（§24.2）。同一产品的同一版本、同一币种只保留一条。"""

    __tablename__ = "prices"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "version", "currency", name="uq_prices_product_version_currency"
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index("ix_prices_product_active", "product_id", "is_active"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base, UUIDPrimaryKey, Timestamped):
    """用户购买的不可变快照（§24.2）。`idempotency_key` 抑制重复下单。"""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_in("status", OrderStatus), name="status_valid"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_idempotency_key"),
        CheckConstraint("total_amount >= 0", name="total_amount_non_negative"),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OrderStatus.PENDING.value, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderItem(Base, UUIDPrimaryKey, Timestamped):
    """订单行：产品、计价版本与授予权益的不可变快照（§24.2）。"""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("unit_amount >= 0", name="unit_amount_non_negative"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(_in("grant_scope_type", ScopeType), name="grant_scope_type_valid"),
        CheckConstraint(_in("grant_capability", Capability), name="grant_capability_valid"),
        Index("ix_order_items_order", "order_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    price_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prices.id"), nullable=False)
    # 下单时的标题、单价与币种快照，产品后续改动不影响既有订单。
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    grant_scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    grant_scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    grant_capability: Mapped[str] = mapped_column(String(32), nullable=False)
    grant_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PaymentAttempt(Base, UUIDPrimaryKey, Timestamped):
    """某次支付请求与渠道状态（§24.2、§24.3）。

    外部支付与本地数据库不构成原子事务：允许"渠道已成功、本地待确认"
    （`PaymentAttemptStatus.PENDING_CONFIRMATION`）。
    `(channel, channel_txn_id)` 唯一，重复回调命中同一渠道交易 ID 时不再重复授予权益。
    """

    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint(_in("status", PaymentAttemptStatus), name="status_valid"),
        UniqueConstraint("channel", "channel_txn_id", name="uq_payment_attempts_channel_txn"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index("ix_payment_attempts_order_status", "order_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PaymentAttemptStatus.CREATED.value, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # 渠道交易 ID；未确认前可为空。不存密钥或签名原文。
    channel_txn_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # 渠道侧支付单号或收银台引用（展示用，非敏感）。
    channel_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Refund(Base, UUIDPrimaryKey, Timestamped):
    """退款申请、渠道处理及确认结果（§24.2、§24.3）。

    未确认前记录处理中；确认后按政策撤销对应权益，不误撤其他来源的权益。
    """

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(_in("status", RefundStatus), name="status_valid"),
        UniqueConstraint("requested_by", "idempotency_key", name="uq_refunds_user_idempotency_key"),
        UniqueConstraint("channel_refund_id", name="uq_refunds_channel_refund_id"),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_refunds_order_status", "order_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    payment_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id"), nullable=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RefundStatus.REQUESTED.value, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    channel_refund_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Settlement(Base, UUIDPrimaryKey, Timestamped):
    """某周期按已批准规则计算的创作者收益（§24.2、§24.3）。

    待结算、冻结、可提现与已提现由 `status` 分开；`(creator_id, period_start, period_end, currency)`
    唯一保证同一周期结算幂等。比例等规则待确认（D16），未配置时结算接口抛 NotEnabled。
    """

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint(_in("status", SettlementStatus), name="status_valid"),
        UniqueConstraint(
            "creator_id",
            "period_start",
            "period_end",
            "currency",
            name="uq_settlements_creator_period_currency",
        ),
        CheckConstraint("gross_amount >= 0", name="gross_amount_non_negative"),
        CheckConstraint("platform_fee >= 0", name="platform_fee_non_negative"),
        CheckConstraint("net_amount >= 0", name="net_amount_non_negative"),
        Index("ix_settlements_creator_status", "creator_id", "status"),
    )

    creator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SettlementStatus.PENDING.value, index=True
    )
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    platform_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    # 使用的已批准规则标识/版本；未配置规则时不产生结算。
    rule_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)


class LedgerEntry(Base, UUIDPrimaryKey):
    """不可随意改写的记账分录（§24.2、§24.3）。

    追加写入，只有 `created_at`；已入账金额通过反向分录更正，不直接修改历史记录。
    分录按 `entry_group_id` 成组，同组借贷方向金额平衡。
    `reverses_entry_id` 关联被冲正的分录；`dedupe_key` 保证同一业务只入账一次。
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(_in("account", LedgerAccount), name="account_valid"),
        CheckConstraint(_in("direction", LedgerDirection), name="direction_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint("dedupe_key", name="uq_ledger_entries_dedupe_key"),
        Index("ix_ledger_entries_account_created", "account", "account_id", "created_at"),
        Index("ix_ledger_entries_business", "business_type", "business_id"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    # 平台账户为 NULL；用户/创作者账户为对应 users.id。
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # 业务引用：order_paid / refund_reversal / settlement / withdrawal_hold / withdrawal_reversal。
    business_type: Mapped[str] = mapped_column(String(60), nullable=False)
    business_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    entry_group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_entries.id"), nullable=True
    )
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("settlements.id"), nullable=True
    )
    memo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Withdrawal(Base, UUIDPrimaryKey, Timestamped):
    """提现申请与处理状态（§24.2、§24.3）。最低提现额待确认（D16），未配置时抛 NotEnabled。"""

    __tablename__ = "withdrawals"
    __table_args__ = (
        CheckConstraint(_in("status", WithdrawalStatus), name="status_valid"),
        UniqueConstraint(
            "creator_id", "idempotency_key", name="uq_withdrawals_creator_idempotency_key"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_withdrawals_creator_status", "creator_id", "status"),
    )

    creator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WithdrawalStatus.REQUESTED.value, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    handled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
