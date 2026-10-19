"""licensing 表定义（用户权责体系 §15.3、§14.4、§6—§8）。

本模块只记录**合同与授权事实**，不触碰资金：金额字段只用于记录双方确认过的报价，
不代表已收款、可结算或已产生应付。

建模约定（沿用项目风格）：
- 状态列以 `String` 存 `app.core.enums` 的枚举值，并配 `CheckConstraint`（取值唯一来源）。
- 跨模块外键只写字符串表名（docs/ARCHITECTURE.md §2.2），不导入对方模型类。
- 协议版本、收益规则版本、商品组成版本一经生效/发布即不可改，变更生成新版本
  （§6.3、§7.3、§14.4 不变量 3）；历史交易按其快照核算，不按当前值重算（D11）。

与文档 §15.3 的偏差（已在报告说明）：
- `price_proposals` 文档写 `sku_id`，本项目**没有 SKU 表**，商品实体是 `commerce.products`，
  因此列名为 `product_id` 并外键到 `products.id`。
- `product_components` 文档写 `resource_revision_id`；本项目内容版本表为 `resource_versions`，
  故列名 `resource_version_id`。`asset_version_id` 项目暂无对应表，保存引用但不加外键。
- party（平台/贡献者主体）目前无独立表：贡献者主体取 `users.id`，平台主体用固定哨兵值
  （见 `service.PLATFORM_PARTY_ID`），故 party 列是普通 `Uuid`，无外键可引。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
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

from app.core.enums import AgreementState, PriceProposalState
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "LicenseAgreement",
    "AgreementVersion",
    "AgreementAcceptance",
    "PriceProposal",
    "PriceAcceptance",
    "RevenueRuleVersion",
    "ProductCompositionVersion",
    "ProductComponent",
    "FreeLicenseAcceptance",
    "CompositionState",
]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """由枚举生成 CheckConstraint 表达式，取值以 app.core.enums 为唯一来源。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class CompositionState(StrEnum):
    """商品组成版本状态。模块内局部枚举，避免改动共享的 app.core.enums
    （沿用 moderation.AppealStatus 的做法）：文档 §15.3 只要求 `state`，未在 core 定义枚举。
    """

    FROZEN = "frozen"
    SUPERSEDED = "superseded"


class LicenseAgreement(Base, UUIDPrimaryKey, Timestamped):
    """商业授权协议主体（§6.2、§15.3）。协议主体稳定；投稿不自动转让版权（D06）。

    协议本身不产生版权转移，只记录平台与贡献者之间的销售授权关系。
    """

    __tablename__ = "license_agreements"
    __table_args__ = (
        CheckConstraint(_in("state", AgreementState), name="state_valid"),
        Index("ix_license_agreements_contributor", "contributor_party_id"),
    )

    # 平台主体哨兵值（见 service.PLATFORM_PARTY_ID）；无 parties 表，故为普通 Uuid。
    platform_party_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # 贡献者主体：取贡献者 users.id。
    contributor_party_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AgreementState.DRAFT.value
    )


class AgreementVersion(Base, UUIDPrimaryKey, Timestamped):
    """协议版本快照（§6.3、§15.3）。

    生效后不可改：比例、授权范围、期限等变化创建新 `version_no`，原版本继续服务其历史交易。
    本模块不提供任何原地覆盖版本的接口。
    """

    __tablename__ = "agreement_versions"
    __table_args__ = (
        UniqueConstraint(
            "agreement_id", "version_no", name="uq_agreement_versions_agreement_version_no"
        ),
        CheckConstraint(_in("state", AgreementState), name="state_valid"),
        Index("ix_agreement_versions_agreement_created", "agreement_id", "created_at"),
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("license_agreements.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 协议全文与附件的服务端承诺哈希；确认时客户端 accepted_hash 必须与之一致（§6.3、§16.2）。
    terms_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 授权权利类型/范围（结构化），如为销售与履约所需的复制、信息网络传播、预览等。
    rights_scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    territory: Mapped[str | None] = mapped_column(String(120), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AgreementState.AWAITING_ACCEPTANCE.value
    )


class AgreementAcceptance(Base, UUIDPrimaryKey, Timestamped):
    """协议版本确认（§6.3、§15.3）。同一版本同一主体只有一条确认。

    双方确认同一版本后版本才生效（§14.1）；终止/到期不删历史确认。
    """

    __tablename__ = "agreement_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "agreement_version_id", "party_id", name="uq_agreement_acceptances_version_party"
        ),
        Index("ix_agreement_acceptances_version", "agreement_version_id"),
    )

    agreement_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agreement_versions.id"), nullable=False, index=True
    )
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # 实际操作者账号；用于审计，不改变 party 的合同归属。
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    accepted_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PriceProposal(Base, UUIDPrimaryKey, Timestamped):
    """价格方案版本（§7、§15.3）。改价重建版本，不覆盖旧版（D08、不变量 3）。

    金额以**整数分**（CNY 分）记录，沿用原支付基线（§9.1「CNY 整数分」）与既有
    `case_measures.amount_fen` 的约定，不使用浮点。字段名沿用 §15.3 的 `_fen`。
    """

    __tablename__ = "price_proposals"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "version_no", name="uq_price_proposals_product_version_no"
        ),
        CheckConstraint(_in("state", PriceProposalState), name="state_valid"),
        CheckConstraint("suggested_fen > 0", name="suggested_fen_positive"),
        CheckConstraint("final_fen IS NULL OR final_fen > 0", name="final_fen_positive"),
        Index("ix_price_proposals_product_state", "product_id", "state"),
    )

    # 文档 §15.3 写 sku_id；本项目没有 SKU 表，商品实体是 commerce.products。
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    final_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 关联的协议版本；价格生效需有效协议（§14.1）。
    agreement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agreement_versions.id"), nullable=True, index=True
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PriceProposalState.PROPOSED.value
    )


class PriceAcceptance(Base, UUIDPrimaryKey, Timestamped):
    """价格方案确认（§7.1、§15.3）。双方对**相同方案**确认；确认方身份由服务端判定（§16.4）。

    未双向确认不得发布对应价格（D08）：仅一方确认或双方确认不同哈希都不生效（§19 T07）。
    """

    __tablename__ = "price_acceptances"
    __table_args__ = (
        UniqueConstraint("proposal_id", "party_id", name="uq_price_acceptances_proposal_party"),
        Index("ix_price_acceptances_proposal", "proposal_id"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("price_proposals.id"), nullable=False, index=True
    )
    party_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    accepted_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RevenueRuleVersion(Base, UUIDPrimaryKey, Timestamped):
    """收益规则版本（§9.2、§15.3）。`creator_bps` 为 0—10000 基点。

    比例、基数、成本等**没有任何默认生产值**（D09、§1.3）：发布接口必须显式给出
    `creator_bps`，未配置时不存在默认比例。发布后不可改，变更生成新 `version_no`。
    """

    __tablename__ = "revenue_rule_versions"
    __table_args__ = (
        UniqueConstraint("version_no", name="uq_revenue_rule_versions_version_no"),
        CheckConstraint(
            "creator_bps >= 0 AND creator_bps <= 10000", name="creator_bps_range"
        ),
    )

    base_definition: Mapped[str] = mapped_column(Text, nullable=False)
    deductions: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    rounding_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    maturity_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductCompositionVersion(Base, UUIDPrimaryKey, Timestamped):
    """商品组成版本（§8.2、§15.3）。每版本组成冻结；新增文件不能暗中加入旧订单。

    一个组件缺少授权，整个对应报价不得进入可售状态，无部分绕过（§19 T10）。
    """

    __tablename__ = "product_composition_versions"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "version_no",
            name="uq_product_composition_versions_product_version_no",
        ),
        CheckConstraint(_in("state", CompositionState), name="state_valid"),
        Index("ix_product_composition_versions_product", "product_id"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CompositionState.FROZEN.value
    )
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ProductComponent(Base, UUIDPrimaryKey, Timestamped):
    """组成组件（§8.2、§15.3）。权重非负且至少一项正数（跨行由服务层校验）。

    「组合与组件唯一」：同一组成版本内同一 (资源版本, 文件版本) 只出现一次。
    """

    __tablename__ = "product_components"
    __table_args__ = (
        UniqueConstraint(
            "composition_version_id",
            "resource_version_id",
            "asset_version_id",
            name="uq_product_components_component",
        ),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        Index("ix_product_components_composition", "composition_version_id"),
    )

    composition_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_composition_versions.id"), nullable=False, index=True
    )
    # 项目内容版本表为 resource_versions（文档 §15.3 的 resource_revision_id）。
    resource_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=True, index=True
    )
    # 项目暂无 asset_versions 表：保存引用，不加外键（文档 §15.3 asset_version_id）。
    asset_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # 组件所依据的销售许可协议版本；为空表示该组件尚无销售许可（不可售）。
    agreement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agreement_versions.id"), nullable=True, index=True
    )
    weight: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)


class FreeLicenseAcceptance(Base, UUIDPrimaryKey, Timestamped):
    """免费共享许可确认（§6.1、§15.3）。与商业协议**独立**：免费不静默转为付费（D05）。

    免费内容不走零元支付订单，另记免费许可；未在此登记免费许可不能推定可收费。
    """

    __tablename__ = "free_license_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "revision_id", "license_version", name="uq_free_license_acceptances_revision_license"
        ),
        Index("ix_free_license_acceptances_revision", "revision_id"),
    )

    # 项目内容版本表为 resource_versions（文档 §15.3 的 revision_id）。
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    license_version: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
