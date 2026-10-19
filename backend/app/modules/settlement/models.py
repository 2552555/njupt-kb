"""settlement 表定义（《用户权责、内容治理与授权收益体系设计文档》§12、§14.4、§15.5、§16.5）。

结算单、付款计划、付款尝试、追偿与税款分开建模；金额一律**整数分**（§15.1「CNY 整数分」
沿用原支付基线），不使用浮点，币种单列。跨模块外键只写字符串表名（docs/ARCHITECTURE.md §2.2），
不导入其他模块模型类。

建模约定（沿用项目风格）：
- 状态列以 `String` 存枚举值并配 `CheckConstraint`，取值以 `app.core.enums` 为唯一来源；
  文档未在 core 定义的少量状态（追偿、税款）在模块内定义局部枚举，避免改动共享枚举。
- `settlement_statements` 批准后**变更新版本**，旧版本保留、不原地改（§15.5、§14.4 不变量 6）。
- `payouts` 同结算版本的有效付款计划唯一；`payout_attempts` 的 `external_ref_key`
  按渠道账户维度唯一，保证一次真实转出不被重复记录（§15.5、§12.3）。
- `recovery_claims`、`tax_records` 均为追加式事实记录；追偿禁止无依据自动扣（§11.5、§12.3）。
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PayoutStatus, SettlementStatementState
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "RecoveryClaimState",
    "TaxRecordState",
    "SettlementStatement",
    "SettlementItem",
    "Payout",
    "PayoutAttempt",
    "RecoveryClaim",
    "TaxRecord",
]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """由枚举生成 CheckConstraint 表达式，取值以枚举为唯一来源。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class RecoveryClaimState(StrEnum):
    """追偿状态（模块内局部枚举；§15.5 只要求 `state`，未在 app.core.enums 定义）。

    已付报酬随后退款时建立与已付及冲回关联的追偿记录；无合法依据不得自动扣（§11.5）。
    """

    OPEN = "open"
    RECOVERED = "recovered"
    WAIVED = "waived"


class TaxRecordState(StrEnum):
    """税款记录状态（§12.3「不虚构已缴」）：默认 pending，仅在真实凭证支持下置 paid。"""

    PENDING = "pending"
    PAID = "paid"
    VOID = "void"


class SettlementStatement(Base, UUIDPrimaryKey, Timestamped):
    """结算单（§15.5）：按合同收款主体／期间／币种生成，批准后变更新版本。

    `(payee_party_id, period, currency, version)` 唯一；金额为整数分。
    `gross_fen` 为纳入本批的可结算净额合计，`tax_fen` 为单独列示的税款，
    `net_fen = gross_fen − tax_fen`（首期未配置税务处理时 `tax_fen=0`，不虚构已缴）。
    """

    __tablename__ = "settlement_statements"
    __table_args__ = (
        UniqueConstraint(
            "payee_party_id",
            "period",
            "currency",
            "version",
            name="uq_settlement_statements_payee_period_currency_version",
        ),
        CheckConstraint(_in("state", SettlementStatementState), name="state_valid"),
        CheckConstraint("gross_fen >= 0", name="gross_fen_non_negative"),
        CheckConstraint("tax_fen >= 0", name="tax_fen_non_negative"),
        CheckConstraint("net_fen >= 0", name="net_fen_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_settlement_statements_payee_state", "payee_party_id", "state"),
    )

    # 合同收款主体；本项目无独立 parties 表，贡献者主体取 users.id，故为普通 Uuid。
    payee_party_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # 结算期间，形如 2026-10；不使用单一「当前周期」隐式状态。
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # 同一主体／期间／币种的版本号；批准后需要变更时新建 version+1，旧版本保留。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SettlementStatementState.AWAITING_CONFIRMATION.value
    )
    gross_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SettlementItem(Base, UUIDPrimaryKey, Timestamped):
    """结算明细（§15.5）：精确指向一个收益 allocation 及本次金额。

    `(statement_id, allocation_id)` 唯一；同一 allocation 可分期结算，但跨批累计锁定＋已付
    不得超过其可付净额（由服务层在批准时以分配预留校验，§14.4 不变量 5）。
    """

    __tablename__ = "settlement_items"
    __table_args__ = (
        UniqueConstraint("statement_id", "allocation_id", name="uq_settlement_items_statement_allocation"),
        CheckConstraint("amount_fen > 0", name="amount_fen_positive"),
        Index("ix_settlement_items_statement", "statement_id"),
        Index("ix_settlement_items_allocation", "allocation_id"),
    )

    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("settlement_statements.id"), nullable=False, index=True
    )
    # 跨模块引用：commerce 模块的 order_item_allocations（只写字符串表名）。
    allocation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_item_allocations.id"), nullable=False, index=True
    )
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)


class Payout(Base, UUIDPrimaryKey, Timestamped):
    """付款计划（§15.5）：一笔付款对应一个结算单版本与收款主体。

    同结算版本的有效付款计划唯一（`statement_version_id` 唯一）；`payee_snapshot`
    冻结对外付款账户，账户变更不自动改变未决付款（§12.3）。
    """

    __tablename__ = "payouts"
    __table_args__ = (
        UniqueConstraint("statement_version_id", name="uq_payouts_statement_version"),
        CheckConstraint(_in("state", PayoutStatus), name="state_valid"),
        CheckConstraint("amount_fen >= 0", name="amount_fen_non_negative"),
    )

    statement_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("settlement_statements.id"), nullable=False, index=True
    )
    # 冻结的收款主体账户快照（JSON）；对外只展示脱敏尾号。
    payee_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PayoutStatus.CREATED.value, index=True
    )


class PayoutAttempt(Base, UUIDPrimaryKey, Timestamped):
    """付款尝试（§15.5、§12.3）：真实转出结果只由管理员凭证据核对写入。

    `(payout_id, attempt_no)` 唯一；`external_ref_key` 按渠道账户维度唯一，
    保证同一次真实转出不被重复记录、不重复付款（§14.4 不变量 3）。
    """

    __tablename__ = "payout_attempts"
    __table_args__ = (
        UniqueConstraint("payout_id", "attempt_no", name="uq_payout_attempts_payout_attempt_no"),
        UniqueConstraint("external_ref_key", name="uq_payout_attempts_external_ref_key"),
        CheckConstraint(_in("state", PayoutStatus), name="state_valid"),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        Index("ix_payout_attempts_payout", "payout_id"),
    )

    payout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payouts.id"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PayoutStatus.PROCESSING.value
    )
    # 真实转出唯一键（渠道账户维度）；只在核对成功时凭证据写入。
    external_ref_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 真实证据引用（渠道流水/银行回单等）；成功核对必填。
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)


class RecoveryClaim(Base, UUIDPrimaryKey, Timestamped):
    """追偿（§15.5、§11.5）：已付报酬随后退款时，与已付及冲回关联，单独记录。

    禁止无依据自动扣：`legal_basis_ref` 必填（服务层校验），`paid_reference` 指向已付事实。
    """

    __tablename__ = "recovery_claims"
    __table_args__ = (
        UniqueConstraint("allocation_id", "refund_id", name="uq_recovery_claims_allocation_refund"),
        CheckConstraint(_in("state", RecoveryClaimState), name="state_valid"),
        CheckConstraint("amount_fen > 0", name="amount_fen_positive"),
        Index("ix_recovery_claims_allocation", "allocation_id"),
        Index("ix_recovery_claims_refund", "refund_id"),
    )

    # 跨模块引用：commerce 的 order_item_allocations 与 refunds（只写字符串表名）。
    allocation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_item_allocations.id"), nullable=False, index=True
    )
    refund_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("refunds.id"), nullable=False, index=True
    )
    # 已付事实引用（结算单/付款/尝试）；保留已付历史，不改写已付记录（§11.5）。
    paid_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    # 追偿依据（合同/事实/适法依据）；无依据不得自动扣。
    legal_basis_ref: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RecoveryClaimState.OPEN.value
    )


class TaxRecord(Base, UUIDPrimaryKey, Timestamped):
    """税款与凭证（§15.5、§12.3）：单列记录，不虚构已缴。

    默认 `state=pending`；只有在真实税务处理及凭证支持时才置 `paid`。
    """

    __tablename__ = "tax_records"
    __table_args__ = (
        CheckConstraint(_in("state", TaxRecordState), name="state_valid"),
        CheckConstraint("amount_fen >= 0", name="amount_fen_non_negative"),
        Index("ix_tax_records_party", "party_id"),
        Index("ix_tax_records_statement", "statement_id"),
    )

    # 纳税主体；本项目无独立 parties 表，取 users.id。
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # 关联结算单；可为空（非批次税项）。
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("settlement_statements.id"), nullable=True, index=True
    )
    tax_type: Mapped[str] = mapped_column(String(60), nullable=False)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 完税/扣缴凭证引用；未提供时不视为已缴。
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TaxRecordState.PENDING.value
    )
