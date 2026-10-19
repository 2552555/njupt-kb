"""settlement 请求/响应 DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象；金额以**整数分**表示（§15.1），不使用浮点。
账户信息只对外返回脱敏尾号（§15.5、§17.3）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# 请求
# --------------------------------------------------------------------------- #
class StatementGenerateIn(_InputModel):
    """后台生成结算草稿（§16.3）：按成熟和预留过滤，只纳入可结算净额。"""

    payee_party_id: uuid.UUID
    period: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)


class StatementApproveIn(_InputModel):
    """后台批准并锁批（§16.3）：财务权限与版本校验。"""

    expected_version: int = Field(ge=1)
    # 可在此补充/覆盖收款账户快照；未提供则沿用生成时的快照。
    payee_snapshot: dict[str, Any] | None = None


class StatementConfirmIn(_InputModel):
    """本人确认结算明细（§16.2）：版本校验。"""

    expected_version: int = Field(ge=1)


class DisputeItemIn(_InputModel):
    allocation_id: uuid.UUID
    amount_fen: int = Field(gt=0)


class StatementDisputeIn(_InputModel):
    """提出结算异议（§16.2）：关联争议行与金额；置 disputed（先回调查）。"""

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    items: list[DisputeItemIn] = Field(min_length=1)


class PayoutAttemptCreateIn(_InputModel):
    """预留对外付款操作（§16.3）。真实转出键只在核对成功时凭证据写入。"""

    note: str | None = Field(default=None, max_length=500)


class PayoutReconcileIn(_InputModel):
    """核对付款结果（§16.3）：真实证据必填；成功必须给出真实转出唯一键。"""

    result: Literal["succeeded", "failed", "uncertain"]
    evidence_ref: str = Field(min_length=1, max_length=512)
    external_ref_key: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)


class LedgerCorrectionIn(_InputModel):
    """提出及确认账务更正（§16.3）：关联原记录，不提供直接改余额。"""

    original_journal_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------- #
# 响应
# --------------------------------------------------------------------------- #
class SettlementItemOut(_ORMModel):
    id: uuid.UUID
    statement_id: uuid.UUID
    allocation_id: uuid.UUID
    amount_fen: int


class SettlementStatementOut(_ORMModel):
    id: uuid.UUID
    payee_party_id: uuid.UUID
    period: str
    currency: str
    version: int
    state: str
    gross_fen: int
    tax_fen: int
    net_fen: int
    created_at: datetime
    updated_at: datetime


class MySettlementStatementOut(SettlementStatementOut):
    """本人视角：附脱敏账户、付款状态与明细（§16.2「脱敏账户」）。"""

    masked_account: str | None = None
    payout_state: str | None = None
    items: list[SettlementItemOut] = Field(default_factory=list)


class PayoutOut(_ORMModel):
    id: uuid.UUID
    statement_version_id: uuid.UUID
    payee_snapshot: dict[str, Any]
    amount_fen: int
    state: str
    created_at: datetime
    updated_at: datetime


class PayoutAttemptOut(_ORMModel):
    id: uuid.UUID
    payout_id: uuid.UUID
    attempt_no: int
    state: str
    external_ref_key: str | None = None
    evidence_ref: str | None = None
    created_at: datetime
    updated_at: datetime


class RecoveryClaimOut(_ORMModel):
    id: uuid.UUID
    allocation_id: uuid.UUID
    refund_id: uuid.UUID
    paid_reference: str | None = None
    amount_fen: int
    legal_basis_ref: str
    state: str
    created_at: datetime


class TaxRecordOut(_ORMModel):
    id: uuid.UUID
    party_id: uuid.UUID
    statement_id: uuid.UUID | None = None
    tax_type: str
    amount_fen: int
    evidence_ref: str | None = None
    state: str
    created_at: datetime


class LedgerCorrectionOut(BaseModel):
    """账务更正结果：反向分录关联原 journal，不提供直接改余额（§16.3）。"""

    journal_id: uuid.UUID
    original_journal_id: uuid.UUID
    event_type: str
    reason: str
    reversed_fen: int
