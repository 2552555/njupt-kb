"""licensing 请求/响应 DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象（§10）。
金额以**整数分**表示（沿用原支付基线 §9.1），不使用浮点。
`accepted_hash` 由客户端提交，但服务端按会话与签约授权判定确认方（§16.4），
不接受客户端指定 actor_party。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AgreementState, PriceProposalState


class _InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# 协议（§6）
# --------------------------------------------------------------------------- #


class AgreementCreateIn(_InputModel):
    title: str = Field(min_length=1, max_length=300)
    # 贡献者主体（取贡献者 users.id）。
    contributor_party_id: uuid.UUID
    # 平台主体哨兵值；缺省由服务端填 PLATFORM_PARTY_ID。
    platform_party_id: uuid.UUID | None = None


class AgreementVersionCreateIn(_InputModel):
    terms_hash: str = Field(min_length=1, max_length=64)
    rights_scope: dict = Field(default_factory=dict)
    territory: str | None = Field(default=None, max_length=120)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class AgreementAcceptanceIn(_InputModel):
    accepted_hash: str = Field(min_length=1, max_length=64)
    evidence_ref: str | None = Field(default=None, max_length=512)


class AgreementVersionOut(_ORMModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    version_no: int
    terms_hash: str
    rights_scope: dict
    territory: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    state: AgreementState
    created_at: datetime
    updated_at: datetime


class AgreementOut(_ORMModel):
    id: uuid.UUID
    platform_party_id: uuid.UUID
    contributor_party_id: uuid.UUID
    title: str
    state: AgreementState
    created_at: datetime
    updated_at: datetime


class AgreementDetailOut(AgreementOut):
    """本人视角：附上历史版本（只含本人参与的主体，不返回他人商业条款）。"""

    versions: list[AgreementVersionOut] = Field(default_factory=list)


class AgreementAcceptanceOut(_ORMModel):
    id: uuid.UUID
    agreement_version_id: uuid.UUID
    party_id: uuid.UUID
    actor_id: uuid.UUID
    accepted_hash: str
    evidence_ref: str | None = None
    accepted_at: datetime


# --------------------------------------------------------------------------- #
# 价格方案（§7）
# --------------------------------------------------------------------------- #


class PriceProposalCreateIn(_InputModel):
    suggested_fen: int = Field(gt=0)
    agreement_version_id: uuid.UUID | None = None


class PriceAcceptanceIn(_InputModel):
    accepted_hash: str = Field(min_length=1, max_length=64)


class PriceRejectionIn(_InputModel):
    """否决价格版本（beta16 §7.1）：理由必填，写入审计并对签约方可见。"""

    reason: str = Field(min_length=1, max_length=500)


class PriceProposalOut(_ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    # 权利方主体（商品 owner）；平台自营商品为空。
    contributor_party_id: uuid.UUID | None = None
    version_no: int
    suggested_fen: int
    final_fen: int | None = None
    agreement_version_id: uuid.UUID | None = None
    state: PriceProposalState
    # 服务端对该版本计算的承诺哈希；客户端确认时须回传一致的值。
    proposal_hash: str
    created_at: datetime
    updated_at: datetime


class PriceAcceptanceOut(_ORMModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    party_id: uuid.UUID
    accepted_hash: str
    accepted_at: datetime


# --------------------------------------------------------------------------- #
# 收益规则（§9.2）
# --------------------------------------------------------------------------- #


class RevenueRuleVersionCreateIn(_InputModel):
    base_definition: str = Field(min_length=1, max_length=2000)
    deductions: str | None = Field(default=None, max_length=2000)
    # 必须显式给出；未配置时不存在默认比例（D09、§1.3）。
    creator_bps: int | None = Field(default=None, ge=0, le=10000)
    rounding_policy: str = Field(min_length=1, max_length=64)
    maturity_policy: str = Field(min_length=1, max_length=64)


class RevenueRuleVersionOut(_ORMModel):
    id: uuid.UUID
    base_definition: str
    deductions: str | None = None
    creator_bps: int
    rounding_policy: str
    maturity_policy: str
    version_no: int
    created_at: datetime


# --------------------------------------------------------------------------- #
# 商品组成（§8.2）
# --------------------------------------------------------------------------- #


class ComponentIn(_InputModel):
    resource_version_id: uuid.UUID | None = None
    asset_version_id: uuid.UUID | None = None
    agreement_version_id: uuid.UUID | None = None
    # 权重非负由 service 校验（业务规则不放在 DTO），全零/负值统一抛 InvalidInput。
    weight: Decimal


class CompositionVersionCreateIn(_InputModel):
    product_id: uuid.UUID
    components: list[ComponentIn] = Field(min_length=1)


class ComponentOut(_ORMModel):
    id: uuid.UUID
    resource_version_id: uuid.UUID | None = None
    asset_version_id: uuid.UUID | None = None
    agreement_version_id: uuid.UUID | None = None
    weight: Decimal


class CompositionVersionOut(_ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    version_no: int
    state: str
    manifest_hash: str
    components: list[ComponentOut] = Field(default_factory=list)
    created_at: datetime
