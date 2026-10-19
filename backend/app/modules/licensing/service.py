"""licensing 业务规则（用户权责体系 §6—§8、§14.1/§14.4、§15.3、§16.2/§16.3、§19 T05—T11）。

业务规则的唯一落点；`router.py` 只做解析与调用。

边界：
- 本模块只记录合同与授权事实，不触碰资金。真实收费与结算受 §2.5 G1—G8 门槛约束：
  当前 `COMMERCE_ENABLED=False` 且 `COMMERCE_COMPLIANCE_GATE_CLOSED=True`，
  **所有对外写接口**在开关关闭时抛 `NotEnabled`（不返回假成功）；读接口仍可返回本机虚构数据。
- 分成比例等没有任何默认生产值（`REVENUE_DEFAULT_CREATOR_BPS=0` 表示未配置），
  未显式给出时抛 `InvalidInput`，绝不套用 80/20 或 85/15 示例值。
- 未双向确认不得发布价格（D08）；历史交易按其快照核算（D11）。

不变量（§14.4）：
1. 无有效销售许可、确认价格或收益规则不能创建新付费订单（`assert_sellable`）。
2. 旧订单不被新价格/新比例/新组件/新主体覆盖（本模块只追加新版本，不覆盖旧版本）。
3. 生效协议/比例被修改 → 生成新版本，旧版本保持。
4. 一流水仅确认一次：唯一约束（版本/方案 + 主体）+ 可选 `Idempotency-Key`。

关于域事件：beta16 起 `DomainEventType` 已提供授权与定价的四个取值
（`AGREEMENT_ACCEPTED`／`PRICE_PROPOSED`／`PRICE_CONFIRMED`／`PRICE_REJECTED`），
本模块在这些时机各写一条域事件，供 notification 模块转换为站内通知。
事件 payload 只放必要 id 与状态，不放哈希、身份证明或金额明细。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import idempotency as idem
from app.core.audit import record_audit
from app.core.enums import AgreementState, DomainEventType, PriceProposalState
from app.core.errors import Conflict, Forbidden, InvalidInput, NotEnabled, NotFound
from app.core.events import emit_event
from app.core.pagination import (
    CursorPage,
    encode_cursor,
    normalize_page_size,
    seek_descending,
)
from app.core.permissions import AGREEMENT_MANAGE, PRICE_CONFIRM, effective_permissions
from app.modules.licensing.models import (
    AgreementAcceptance,
    AgreementVersion,
    CompositionState,
    FreeLicenseAcceptance,
    LicenseAgreement,
    PriceAcceptance,
    PriceProposal,
    ProductComponent,
    ProductCompositionVersion,
    RevenueRuleVersion,
)
from app.modules.licensing.schemas import (
    AgreementAcceptanceOut,
    AgreementDetailOut,
    AgreementOut,
    AgreementVersionOut,
    ComponentOut,
    CompositionVersionOut,
    PriceProposalOut,
    RevenueRuleVersionOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

__all__ = [
    "PLATFORM_PARTY_ID",
    "assert_sellable",
    "list_my_agreements",
    "accept_agreement",
    "create_price_proposal",
    "accept_price_proposal",
    "admin_accept_price_proposal",
    "reject_price_proposal",
    "create_agreement",
    "create_agreement_version",
    "publish_revenue_rule",
    "freeze_composition",
    "list_admin_agreements",
    "list_admin_price_proposals",
    "record_free_license_acceptance",
]

# 平台主体哨兵值：项目没有独立的 parties 表，贡献者主体取 users.id，
# 平台主体用固定 UUID 表示，保证同一商品/协议的价格确认方稳定可判（§3.2「平台一方授权签约人员」）。
PLATFORM_PARTY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")

# 视为「价格已确认」的状态（可进入报价/上架校验）。
_CONFIRMED_PRICE_STATES = (
    PriceProposalState.CONFIRMED.value,
    PriceProposalState.EFFECTIVE.value,
)

# 改价时置为 SUPERSEDED 的非终态方案；已确认/生效的历史方案不覆盖（D11、不变量 3）。
_OPEN_PRICE_STATES = (
    PriceProposalState.PROPOSED.value,
    PriceProposalState.NEGOTIATING.value,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_enabled() -> None:
    """阶段开关关闭时，一切对外写接口显式失败（§2.5、§25.2），不返回假成功。"""
    if not settings.COMMERCE_ENABLED or settings.COMMERCE_COMPLIANCE_GATE_CLOSED:
        raise NotEnabled(
            "商业授权与定价功能未启用",
            details={
                "reason": "COMMERCE_ENABLED/COMMERCE_COMPLIANCE_GATE_CLOSED",
                "commerce_enabled": settings.COMMERCE_ENABLED,
                "compliance_gate_closed": settings.COMMERCE_COMPLIANCE_GATE_CLOSED,
            },
        )


def _normalize_key(idempotency_key: str | None) -> str | None:
    """可选 Idempotency-Key：未提供时不做请求级去重（仍由唯一约束保证业务去重）。"""
    if not idempotency_key or not idempotency_key.strip():
        return None
    return idem.normalize_key(idempotency_key)


def _proposal_hash(
    *,
    product_id: uuid.UUID,
    version_no: int,
    suggested_fen: int,
    agreement_version_id: uuid.UUID | None,
) -> str:
    """价格方案的服务端承诺哈希：确认方须回传一致值（§7.1、§16.4）。"""
    return idem.request_hash(
        {
            "product_id": str(product_id),
            "version_no": int(version_no),
            "suggested_fen": int(suggested_fen),
            "agreement_version_id": str(agreement_version_id)
            if agreement_version_id
            else None,
        }
    )


def _manifest_hash(product_id: uuid.UUID, components: Sequence[Any]) -> str:
    """组成清单承诺哈希：冻结后任何组件变化都改变哈希（§8.2）。"""
    payload = {
        "product_id": str(product_id),
        "components": sorted(
            (
                {
                    "resource_version_id": str(c.resource_version_id)
                    if c.resource_version_id
                    else None,
                    "asset_version_id": str(c.asset_version_id) if c.asset_version_id else None,
                    "agreement_version_id": str(c.agreement_version_id)
                    if c.agreement_version_id
                    else None,
                    "weight": str(Decimal(str(c.weight))),
                }
                for c in components
            ),
            key=lambda item: (
                item["resource_version_id"] or "",
                item["asset_version_id"] or "",
                item["agreement_version_id"] or "",
            ),
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 分页（§25.1）
# --------------------------------------------------------------------------- #
async def _page(db: AsyncSession, stmt, *, model, limit, cursor):
    size = normalize_page_size(limit)
    stmt = seek_descending(db, stmt, model.created_at, model.id, cursor).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"ts": last.created_at.isoformat(), "id": str(last.id)})
    return rows, next_cursor, has_more


# --------------------------------------------------------------------------- #
# 视图构造
# --------------------------------------------------------------------------- #
async def _agreement_detail(db: AsyncSession, agreement: LicenseAgreement) -> AgreementDetailOut:
    versions = list(
        (
            await db.execute(
                select(AgreementVersion)
                .where(AgreementVersion.agreement_id == agreement.id)
                .order_by(AgreementVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    return AgreementDetailOut(
        id=agreement.id,
        platform_party_id=agreement.platform_party_id,
        contributor_party_id=agreement.contributor_party_id,
        title=agreement.title,
        state=agreement.state,
        created_at=agreement.created_at,
        updated_at=agreement.updated_at,
        versions=[AgreementVersionOut.model_validate(v) for v in versions],
    )


def _version_out(version: AgreementVersion) -> AgreementVersionOut:
    return AgreementVersionOut.model_validate(version)


def _proposal_out(proposal: PriceProposal, *, contributor_party_id: uuid.UUID | None) -> PriceProposalOut:
    return PriceProposalOut(
        id=proposal.id,
        product_id=proposal.product_id,
        contributor_party_id=contributor_party_id,
        version_no=proposal.version_no,
        suggested_fen=proposal.suggested_fen,
        final_fen=proposal.final_fen,
        agreement_version_id=proposal.agreement_version_id,
        state=proposal.state,
        proposal_hash=_proposal_hash(
            product_id=proposal.product_id,
            version_no=proposal.version_no,
            suggested_fen=proposal.suggested_fen,
            agreement_version_id=proposal.agreement_version_id,
        ),
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


async def _product_owner_id(db: AsyncSession, product_id: uuid.UUID) -> uuid.UUID | None:
    from app.modules.commerce.models import Product

    return await db.scalar(select(Product.owner_id).where(Product.id == product_id))


# --------------------------------------------------------------------------- #
# 用户/贡献者侧：协议（§6、§16.2）
# --------------------------------------------------------------------------- #
async def list_my_agreements(db: AsyncSession, *, user: "User") -> list[AgreementDetailOut]:
    """本人协议及历史版本（§16.2）。只返回本人作为贡献者主体、或本人确认过的协议，
    不读取他人商业条款。读接口在开关关闭时仍可返回（本机测试用虚构数据），不含资金字段。
    """
    accepted_agreement_ids = (
        select(AgreementVersion.agreement_id)
        .join(
            AgreementAcceptance,
            AgreementAcceptance.agreement_version_id == AgreementVersion.id,
        )
        .where(AgreementAcceptance.actor_id == user.id)
    )
    stmt = (
        select(LicenseAgreement)
        .where(
            or_(
                LicenseAgreement.contributor_party_id == user.id,
                LicenseAgreement.id.in_(accepted_agreement_ids),
            )
        )
        .order_by(LicenseAgreement.created_at.desc(), LicenseAgreement.id.desc())
    )
    agreements = list((await db.execute(stmt)).scalars().all())
    return [await _agreement_detail(db, a) for a in agreements]


async def _party_for_agreement(
    db: AsyncSession, *, user: "User", agreement: LicenseAgreement
) -> uuid.UUID:
    """按会话与签约授权判定确认方主体，不接受客户端传 actor_party（§16.4）。

    本版：贡献者主体由账号本人代表（user.id == contributor_party_id）；平台主体由持有
    `AGREEMENT_MANAGE` 的签约人员代表。代理签约（代人确认）待签约授权模型落地后再扩展。
    """
    if user.id == agreement.contributor_party_id:
        return agreement.contributor_party_id
    granted = await effective_permissions(db, user)
    if AGREEMENT_MANAGE in granted:
        return agreement.platform_party_id
    raise Forbidden("你不是该协议的签约主体，也没有平台签约权限")


async def accept_agreement(
    db: AsyncSession,
    *,
    user: "User",
    version_id: uuid.UUID,
    payload,
    request_id: str,
    idempotency_key: str | None = None,
) -> AgreementAcceptanceOut:
    """确认特定协议全文及附件哈希（§6.3、§16.2）。

    `accepted_hash` 必须与服务端承诺的 `terms_hash` 一致，否则 Conflict。
    双方确认同一版本后版本才生效（§14.1）。
    """
    _require_enabled()
    version = await db.get(AgreementVersion, version_id)
    if version is None:
        raise NotFound("协议版本不存在")
    agreement = await db.get(LicenseAgreement, version.agreement_id)
    if agreement is None:
        raise NotFound("协议不存在")
    if version.state in (AgreementState.EXPIRED.value, AgreementState.TERMINATED.value):
        raise Conflict("该协议版本已失效，不能确认", code="AGREEMENT_VERSION_INACTIVE")

    party_id = await _party_for_agreement(db, user=user, agreement=agreement)
    if payload.accepted_hash != version.terms_hash:
        raise Conflict("确认哈希与协议版本不一致", code="ACCEPTED_HASH_MISMATCH")

    key = _normalize_key(idempotency_key)
    scope = f"licensing.agreement.accept:{version.id}"
    req = {"party_id": str(party_id), "accepted_hash": payload.accepted_hash}
    cached = await idem.lookup(db, scope=scope, key=key, payload=req, actor_id=user.id) if key else None
    if cached is not None:
        return AgreementAcceptanceOut.model_validate(cached)

    existing = await db.scalar(
        select(AgreementAcceptance).where(
            AgreementAcceptance.agreement_version_id == version.id,
            AgreementAcceptance.party_id == party_id,
        )
    )
    if existing is not None:
        raise Conflict("该主体已确认此协议版本", code="ALREADY_ACCEPTED")

    acceptance = AgreementAcceptance(
        agreement_version_id=version.id,
        party_id=party_id,
        actor_id=user.id,
        accepted_hash=payload.accepted_hash,
        evidence_ref=payload.evidence_ref,
        accepted_at=_now(),
    )
    db.add(acceptance)
    await db.flush()

    # 双方确认同版本才生效（§14.1）；未齐时保持 AWAITING_ACCEPTANCE。
    accepted_parties = set(
        (
            await db.execute(
                select(AgreementAcceptance.party_id).where(
                    AgreementAcceptance.agreement_version_id == version.id
                )
            )
        )
        .scalars()
        .all()
    )
    required = {agreement.contributor_party_id, agreement.platform_party_id}
    both_accepted = required <= accepted_parties
    if both_accepted:
        version.state = AgreementState.ACTIVE.value
        agreement.state = AgreementState.ACTIVE.value

    # 每次主体确认各写一条事件，通知另一签约方；按「版本 + 主体」去重，
    # 重放同一确认不会重复投递（幂等由 unique(event_type, dedupe_key) 兜底）。
    await emit_event(
        db,
        event_type=DomainEventType.AGREEMENT_ACCEPTED,
        subject_type="agreement_version",
        subject_id=version.id,
        subject_version=version.version_no,
        payload={
            "agreement_id": str(agreement.id),
            "agreement_version_id": str(version.id),
            "party_id": str(party_id),
            "actor_id": str(user.id),
            "version_state": version.state,
            "both_accepted": both_accepted,
        },
        dedupe_key=f"agreement-accepted:{version.id}:{party_id}",
    )

    await record_audit(
        db,
        action="licensing.agreement.accepted",
        object_type="agreement_version",
        object_id=version.id,
        object_version=version.version_no,
        actor_id=user.id,
        after={
            "party_id": str(party_id),
            "accepted_hash": payload.accepted_hash,
            "version_state": version.state,
            "agreement_state": agreement.state,
        },
        request_id=request_id,
    )
    await db.flush()

    out = AgreementAcceptanceOut.model_validate(acceptance)
    if key:
        await idem.remember(
            db,
            scope=scope,
            key=key,
            payload=req,
            actor_id=user.id,
            response_status=201,
            response=out.model_dump(mode="json"),
        )
    return out


# --------------------------------------------------------------------------- #
# 用户/贡献者侧：价格方案（§7、§16.2）
# --------------------------------------------------------------------------- #
async def create_price_proposal(
    db: AsyncSession,
    *,
    user: "User",
    product_id: uuid.UUID,
    payload,
    request_id: str,
    idempotency_key: str | None = None,
) -> PriceProposalOut:
    """对应权利方提出建议售价（§7.1）。改价重建版本，不覆盖旧版（D08、不变量 3）。

    本版仅允许权利方本人（商品 owner）提出；获准代理待签约授权模型落地后扩展。
    """
    _require_enabled()
    from app.modules.commerce.models import Product

    product = await db.get(Product, product_id)
    if product is None:
        raise NotFound("商品不存在")
    if product.owner_id != user.id:
        raise Forbidden("只有对应权利方或获准代理可以提出建议售价")
    if payload.suggested_fen <= 0:
        raise InvalidInput("建议售价必须为正数")
    if payload.agreement_version_id is not None:
        if await db.get(AgreementVersion, payload.agreement_version_id) is None:
            raise NotFound("关联的协议版本不存在")

    key = _normalize_key(idempotency_key)
    scope = f"licensing.price.propose:{product_id}"
    req = {
        "suggested_fen": int(payload.suggested_fen),
        "agreement_version_id": str(payload.agreement_version_id)
        if payload.agreement_version_id
        else None,
    }
    cached = await idem.lookup(db, scope=scope, key=key, payload=req, actor_id=user.id) if key else None
    if cached is not None:
        return PriceProposalOut.model_validate(cached)

    last = await db.scalar(
        select(func.max(PriceProposal.version_no)).where(PriceProposal.product_id == product_id)
    )
    version_no = int(last or 0) + 1

    # 改价：旧的非终态方案标记 SUPERSEDED（金额不变、版本保留）；已确认/生效的历史方案不动。
    await db.execute(
        update(PriceProposal)
        .where(
            PriceProposal.product_id == product_id,
            PriceProposal.state.in_(_OPEN_PRICE_STATES),
        )
        .values(state=PriceProposalState.SUPERSEDED.value)
    )

    proposal = PriceProposal(
        product_id=product_id,
        version_no=version_no,
        suggested_fen=int(payload.suggested_fen),
        final_fen=None,
        agreement_version_id=payload.agreement_version_id,
        state=PriceProposalState.PROPOSED.value,
    )
    db.add(proposal)
    await db.flush()

    await record_audit(
        db,
        action="licensing.price.proposed",
        object_type="price_proposal",
        object_id=proposal.id,
        object_version=version_no,
        actor_id=user.id,
        after={
            "product_id": str(product_id),
            "suggested_fen": int(payload.suggested_fen),
            "state": proposal.state,
        },
        request_id=request_id,
    )
    # 提案/重建版本各写一条事件；金额明细留在审计与提案行，事件只放 id 与状态。
    await emit_event(
        db,
        event_type=DomainEventType.PRICE_PROPOSED,
        subject_type="price_proposal",
        subject_id=proposal.id,
        subject_version=version_no,
        payload={
            "product_id": str(product_id),
            "price_proposal_id": str(proposal.id),
            "version_no": version_no,
            "state": proposal.state,
            "proposed_by": str(user.id),
        },
        dedupe_key=f"price-proposed:{proposal.id}",
    )
    await db.flush()

    out = _proposal_out(proposal, contributor_party_id=product.owner_id)
    if key:
        await idem.remember(
            db,
            scope=scope,
            key=key,
            payload=req,
            actor_id=user.id,
            response_status=201,
            response=out.model_dump(mode="json"),
        )
    return out


async def _required_price_parties(db: AsyncSession, proposal: PriceProposal) -> set[uuid.UUID]:
    """价格生效所需确认的主体集合：平台 + 权利方（平台自营商品无权利方）。"""
    required = {PLATFORM_PARTY_ID}
    owner = await _product_owner_id(db, proposal.product_id)
    if owner is not None:
        required.add(owner)
    return required


async def _record_price_acceptance(
    db: AsyncSession,
    *,
    proposal: PriceProposal,
    party_id: uuid.UUID,
    actor_id: uuid.UUID,
    accepted_hash: str,
    request_id: str,
    action: str,
) -> None:
    """记录一次价格确认，并在双方确认同一方案后置 CONFIRMED（§7.1、§19 T07）。"""
    if proposal.state in (
        PriceProposalState.SUPERSEDED.value,
        PriceProposalState.WITHDRAWN.value,
    ):
        raise Conflict("该价格方案已失效，不能确认", code="PRICE_PROPOSAL_INACTIVE")

    expected = _proposal_hash(
        product_id=proposal.product_id,
        version_no=proposal.version_no,
        suggested_fen=proposal.suggested_fen,
        agreement_version_id=proposal.agreement_version_id,
    )
    if accepted_hash != expected:
        raise Conflict("确认哈希与价格方案不一致", code="ACCEPTED_HASH_MISMATCH")

    existing = await db.scalar(
        select(PriceAcceptance).where(
            PriceAcceptance.proposal_id == proposal.id,
            PriceAcceptance.party_id == party_id,
        )
    )
    if existing is not None:
        raise Conflict("该主体已确认此价格方案", code="ALREADY_ACCEPTED")

    db.add(
        PriceAcceptance(
            proposal_id=proposal.id,
            party_id=party_id,
            accepted_hash=accepted_hash,
            accepted_at=_now(),
        )
    )
    await db.flush()

    accepted_parties = set(
        (
            await db.execute(
                select(PriceAcceptance.party_id).where(PriceAcceptance.proposal_id == proposal.id)
            )
        )
        .scalars()
        .all()
    )
    required = await _required_price_parties(db, proposal)
    if required <= accepted_parties:
        # 双方确认同一版本：置 CONFIRMED 并冻结最终金额。
        proposal.state = PriceProposalState.CONFIRMED.value
        proposal.final_fen = proposal.suggested_fen
    elif proposal.state == PriceProposalState.PROPOSED.value:
        # 仅一方确认：进入协商中，价格尚未生效（§19 T07）。
        proposal.state = PriceProposalState.NEGOTIATING.value

    await record_audit(
        db,
        action=action,
        object_type="price_proposal",
        object_id=proposal.id,
        object_version=proposal.version_no,
        actor_id=actor_id,
        after={
            "party_id": str(party_id),
            "accepted_hash": accepted_hash,
            "state": proposal.state,
            "final_fen": proposal.final_fen,
        },
        request_id=request_id,
    )
    if proposal.state == PriceProposalState.CONFIRMED.value:
        # 双方同版本同哈希确认：只在这一刻写一次事件，重试不重复投递。
        await emit_event(
            db,
            event_type=DomainEventType.PRICE_CONFIRMED,
            subject_type="price_proposal",
            subject_id=proposal.id,
            subject_version=proposal.version_no,
            payload={
                "product_id": str(proposal.product_id),
                "price_proposal_id": str(proposal.id),
                "version_no": proposal.version_no,
                "state": proposal.state,
                "party_id": str(party_id),
                "actor_id": str(actor_id),
            },
            dedupe_key=f"price-confirmed:{proposal.id}",
        )
    await db.flush()
    # 状态更新会令 server 端 onupdate 的 updated_at 过期；显式刷新，避免在异步上下文懒加载。
    await db.refresh(proposal)


async def accept_price_proposal(
    db: AsyncSession,
    *,
    user: "User",
    proposal_id: uuid.UUID,
    payload,
    request_id: str,
    idempotency_key: str | None = None,
) -> PriceProposalOut:
    """用户侧确认价格（§16.2）。确认方身份由服务端按会话与签约授权判定，不接受客户端传 actor_party。"""
    _require_enabled()
    proposal = await db.get(PriceProposal, proposal_id)
    if proposal is None:
        raise NotFound("价格方案不存在")

    owner = await _product_owner_id(db, proposal.product_id)
    if owner is not None and user.id == owner:
        party_id = owner
    else:
        granted = await effective_permissions(db, user)
        if PRICE_CONFIRM not in granted:
            raise Forbidden("你不是该价格方案的权利方，也没有价格确认权限")
        party_id = PLATFORM_PARTY_ID

    key = _normalize_key(idempotency_key)
    scope = f"licensing.price.accept:{proposal.id}"
    req = {"party_id": str(party_id), "accepted_hash": payload.accepted_hash}
    cached = await idem.lookup(db, scope=scope, key=key, payload=req, actor_id=user.id) if key else None
    if cached is not None:
        return PriceProposalOut.model_validate(cached)

    await _record_price_acceptance(
        db,
        proposal=proposal,
        party_id=party_id,
        actor_id=user.id,
        accepted_hash=payload.accepted_hash,
        request_id=request_id,
        action="licensing.price.accepted",
    )
    out = _proposal_out(proposal, contributor_party_id=owner)
    if key:
        await idem.remember(
            db,
            scope=scope,
            key=key,
            payload=req,
            actor_id=user.id,
            response_status=201,
            response=out.model_dump(mode="json"),
        )
    return out


async def admin_accept_price_proposal(
    db: AsyncSession,
    *,
    actor: "User",
    proposal_id: uuid.UUID,
    payload,
    request_id: str,
    idempotency_key: str | None = None,
) -> PriceProposalOut:
    """平台确认价格（§16.3）。**不可代表贡献者确认**：若操作者即贡献者主体则 Forbidden。"""
    _require_enabled()
    proposal = await db.get(PriceProposal, proposal_id)
    if proposal is None:
        raise NotFound("价格方案不存在")
    owner = await _product_owner_id(db, proposal.product_id)
    if owner is not None and actor.id == owner:
        raise Forbidden("平台不能代表贡献者确认价格")

    key = _normalize_key(idempotency_key)
    scope = f"licensing.price.accept:{proposal.id}"
    req = {"party_id": str(PLATFORM_PARTY_ID), "accepted_hash": payload.accepted_hash}
    cached = await idem.lookup(db, scope=scope, key=key, payload=req, actor_id=actor.id) if key else None
    if cached is not None:
        return PriceProposalOut.model_validate(cached)

    await _record_price_acceptance(
        db,
        proposal=proposal,
        party_id=PLATFORM_PARTY_ID,
        actor_id=actor.id,
        accepted_hash=payload.accepted_hash,
        request_id=request_id,
        action="licensing.price.platform_confirmed",
    )
    out = _proposal_out(proposal, contributor_party_id=owner)
    if key:
        await idem.remember(
            db,
            scope=scope,
            key=key,
            payload=req,
            actor_id=actor.id,
            response_status=201,
            response=out.model_dump(mode="json"),
        )
    return out


async def reject_price_proposal(
    db: AsyncSession,
    *,
    user: "User",
    proposal_id: uuid.UUID,
    reason: str,
    request_id: str | None,
) -> PriceProposalOut:
    """否决一个尚未确认的价格版本（§7.1）。

    `PriceProposalState` 目前没有 REJECTED 取值（`app/core/enums.py` 本轮冻结，
    新增取值需要迁移），因此复用 `WITHDRAWN` 作为「不可再确认」的终态；
    「否决」与「提案方撤回」的差别只由域事件 `price.rejected` 与审计区分，
    旧版本金额不被覆盖（不变量 3）。

    主体判定与确认一致：权利方本人或持 `PRICE_CONFIRM` 的平台人员；
    已确认/已失效的方案不能否决（Conflict）。
    """
    _require_enabled()
    proposal = await db.get(PriceProposal, proposal_id)
    if proposal is None:
        raise NotFound("价格方案不存在")
    if proposal.state not in _OPEN_PRICE_STATES:
        raise Conflict("该价格方案已失效，不能拒绝", code="PRICE_PROPOSAL_INACTIVE")
    text = (reason or "").strip()
    if not text:
        raise InvalidInput("reason 必填")

    owner = await _product_owner_id(db, proposal.product_id)
    if owner is not None and user.id == owner:
        party_id = owner
    else:
        granted = await effective_permissions(db, user)
        if PRICE_CONFIRM not in granted:
            raise Forbidden("你不是该价格方案的权利方，也没有价格确认权限")
        party_id = PLATFORM_PARTY_ID

    proposal.state = PriceProposalState.WITHDRAWN.value
    await db.flush()
    await record_audit(
        db,
        action="licensing.price.rejected",
        object_type="price_proposal",
        object_id=proposal.id,
        object_version=proposal.version_no,
        actor_id=user.id,
        after={"party_id": str(party_id), "state": proposal.state},
        reason=text,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.PRICE_REJECTED,
        subject_type="price_proposal",
        subject_id=proposal.id,
        subject_version=proposal.version_no,
        payload={
            "product_id": str(proposal.product_id),
            "price_proposal_id": str(proposal.id),
            "version_no": proposal.version_no,
            "state": proposal.state,
            "rejected_by": str(party_id),
            "actor_id": str(user.id),
        },
        dedupe_key=f"price-rejected:{proposal.id}",
    )
    await db.flush()
    # 状态更新会令 onupdate 的 updated_at 过期；显式刷新，避免异步上下文懒加载。
    await db.refresh(proposal)
    return _proposal_out(proposal, contributor_party_id=owner)


# --------------------------------------------------------------------------- #
# 后台侧：协议与版本（§16.3）
# --------------------------------------------------------------------------- #
async def create_agreement(
    db: AsyncSession, *, actor: "User", payload, request_id: str
) -> AgreementOut:
    """创建协议主体（DRAFT）。审核通过 ≠ 版权已转让 ≠ 可任意定价（§1.1）。"""
    _require_enabled()
    agreement = LicenseAgreement(
        platform_party_id=payload.platform_party_id or PLATFORM_PARTY_ID,
        contributor_party_id=payload.contributor_party_id,
        title=payload.title,
        state=AgreementState.DRAFT.value,
    )
    db.add(agreement)
    await db.flush()
    await record_audit(
        db,
        action="licensing.agreement.created",
        object_type="license_agreement",
        object_id=agreement.id,
        actor_id=actor.id,
        after={
            "contributor_party_id": str(agreement.contributor_party_id),
            "platform_party_id": str(agreement.platform_party_id),
            "state": agreement.state,
        },
        request_id=request_id,
    )
    await db.flush()
    return AgreementOut.model_validate(agreement)


async def create_agreement_version(
    db: AsyncSession, *, actor: "User", agreement_id: uuid.UUID, payload, request_id: str
) -> AgreementVersionOut:
    """生成协议新版本（§16.3）。**不覆盖旧版**：旧版本保持原样继续服务其历史交易（§6.3）。"""
    _require_enabled()
    agreement = await db.get(LicenseAgreement, agreement_id)
    if agreement is None:
        raise NotFound("协议不存在")

    last = await db.scalar(
        select(func.max(AgreementVersion.version_no)).where(
            AgreementVersion.agreement_id == agreement_id
        )
    )
    version_no = int(last or 0) + 1
    version = AgreementVersion(
        agreement_id=agreement_id,
        version_no=version_no,
        terms_hash=payload.terms_hash,
        rights_scope=payload.rights_scope or {},
        territory=payload.territory,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        state=AgreementState.AWAITING_ACCEPTANCE.value,
    )
    db.add(version)
    await db.flush()
    if agreement.state == AgreementState.DRAFT.value:
        agreement.state = AgreementState.AWAITING_ACCEPTANCE.value

    await record_audit(
        db,
        action="licensing.agreement.version_created",
        object_type="agreement_version",
        object_id=version.id,
        object_version=version_no,
        actor_id=actor.id,
        after={
            "agreement_id": str(agreement_id),
            "terms_hash": payload.terms_hash,
            "state": version.state,
        },
        request_id=request_id,
    )
    await db.flush()
    return _version_out(version)


# --------------------------------------------------------------------------- #
# 后台侧：收益规则（§9.2）
# --------------------------------------------------------------------------- #
async def publish_revenue_rule(
    db: AsyncSession, *, actor: "User", payload, request_id: str
) -> RevenueRuleVersionOut:
    """发布收益规则版本（§16.3）。`creator_bps` 必须显式给出，无任何默认比例（D09、§1.3）。

    发布后不可改；变更生成新 `version_no`（不变量 3）。
    """
    _require_enabled()
    if payload.creator_bps is None:
        raise InvalidInput(
            "creator_bps 必须显式给出；未配置时不存在默认比例",
            code="CREATOR_BPS_REQUIRED",
        )
    if not 0 <= payload.creator_bps <= 10000:
        raise InvalidInput("creator_bps 必须在 0—10000 之间")

    last = await db.scalar(select(func.max(RevenueRuleVersion.version_no)))
    version_no = int(last or 0) + 1
    rule = RevenueRuleVersion(
        base_definition=payload.base_definition,
        deductions=payload.deductions,
        creator_bps=payload.creator_bps,
        rounding_policy=payload.rounding_policy,
        maturity_policy=payload.maturity_policy,
        version_no=version_no,
    )
    db.add(rule)
    await db.flush()
    await record_audit(
        db,
        action="licensing.revenue_rule.published",
        object_type="revenue_rule_version",
        object_id=rule.id,
        object_version=version_no,
        actor_id=actor.id,
        after={"creator_bps": payload.creator_bps, "version_no": version_no},
        request_id=request_id,
    )
    await db.flush()
    return RevenueRuleVersionOut.model_validate(rule)


# --------------------------------------------------------------------------- #
# 后台侧：商品组成（§8.2）
# --------------------------------------------------------------------------- #
async def freeze_composition(
    db: AsyncSession, *, actor: "User", payload, request_id: str
) -> CompositionVersionOut:
    """冻结商品组成（§16.3）。权重非负且至少一项正数；组件许可缺失不在此放行（由 assert_sellable 拒绝）。"""
    _require_enabled()
    from app.modules.commerce.models import Product

    product = await db.get(Product, payload.product_id)
    if product is None:
        raise NotFound("商品不存在")
    if not payload.components:
        raise InvalidInput("商品组成至少包含一个组件")

    weights = [Decimal(str(c.weight)) for c in payload.components]
    if any(w < 0 for w in weights):
        raise InvalidInput("组件权重必须非负", code="WEIGHT_NEGATIVE")
    if not any(w > 0 for w in weights):
        raise InvalidInput("至少一个组件权重必须为正数", code="WEIGHT_ALL_ZERO")

    for component in payload.components:
        if component.agreement_version_id is not None:
            if await db.get(AgreementVersion, component.agreement_version_id) is None:
                raise NotFound("组件引用的协议版本不存在")

    manifest_hash = _manifest_hash(payload.product_id, payload.components)
    last = await db.scalar(
        select(func.max(ProductCompositionVersion.version_no)).where(
            ProductCompositionVersion.product_id == payload.product_id
        )
    )
    version_no = int(last or 0) + 1
    composition = ProductCompositionVersion(
        product_id=payload.product_id,
        version_no=version_no,
        state=CompositionState.FROZEN.value,
        manifest_hash=manifest_hash,
    )
    db.add(composition)
    await db.flush()

    for component in payload.components:
        db.add(
            ProductComponent(
                composition_version_id=composition.id,
                resource_version_id=component.resource_version_id,
                asset_version_id=component.asset_version_id,
                agreement_version_id=component.agreement_version_id,
                weight=Decimal(str(component.weight)),
            )
        )
    await db.flush()

    await record_audit(
        db,
        action="licensing.composition.frozen",
        object_type="product_composition_version",
        object_id=composition.id,
        object_version=version_no,
        actor_id=actor.id,
        after={
            "product_id": str(payload.product_id),
            "component_count": len(payload.components),
            "manifest_hash": manifest_hash,
        },
        request_id=request_id,
    )
    await db.flush()

    components = list(
        (
            await db.execute(
                select(ProductComponent).where(
                    ProductComponent.composition_version_id == composition.id
                )
            )
        )
        .scalars()
        .all()
    )
    out = CompositionVersionOut.model_validate(composition)
    out.components = [ComponentOut.model_validate(c) for c in components]
    return out


# --------------------------------------------------------------------------- #
# 后台侧：查询（§16.3）
# --------------------------------------------------------------------------- #
async def list_admin_agreements(
    db: AsyncSession, *, limit: int | None, cursor: str | None
) -> CursorPage[AgreementOut]:
    rows, next_cursor, has_more = await _page(
        db, select(LicenseAgreement), model=LicenseAgreement, limit=limit, cursor=cursor
    )
    return CursorPage(
        items=[AgreementOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_admin_price_proposals(
    db: AsyncSession, *, limit: int | None, cursor: str | None,
    owner_id: uuid.UUID | None = None,
) -> CursorPage[PriceProposalOut]:
    stmt = select(PriceProposal)
    if owner_id is not None:
        from app.modules.commerce.models import Product
        stmt = stmt.join(Product, Product.id == PriceProposal.product_id).where(Product.owner_id == owner_id)
    rows, next_cursor, has_more = await _page(
        db, stmt, model=PriceProposal, limit=limit, cursor=cursor
    )
    owner_by_product: dict[uuid.UUID, uuid.UUID | None] = {}
    if rows:
        from app.modules.commerce.models import Product

        product_ids = {row.product_id for row in rows}
        for pid, owner in (
            await db.execute(select(Product.id, Product.owner_id).where(Product.id.in_(product_ids)))
        ).all():
            owner_by_product[pid] = owner
    return CursorPage(
        items=[
            _proposal_out(row, contributor_party_id=owner_by_product.get(row.product_id))
            for row in rows
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------- #
# 免费许可（§6.1）——与商业协议独立
# --------------------------------------------------------------------------- #
async def record_free_license_acceptance(
    db: AsyncSession,
    *,
    revision_id: uuid.UUID,
    license_version: str,
    actor_id: uuid.UUID,
) -> FreeLicenseAcceptance:
    """登记免费共享许可确认（§6.1）。免费不静默转为付费（D05）；与商业协议独立。"""
    existing = await db.scalar(
        select(FreeLicenseAcceptance).where(
            FreeLicenseAcceptance.revision_id == revision_id,
            FreeLicenseAcceptance.license_version == license_version,
        )
    )
    if existing is not None:
        return existing
    record = FreeLicenseAcceptance(
        revision_id=revision_id,
        license_version=license_version,
        actor_id=actor_id,
        accepted_at=_now(),
    )
    db.add(record)
    await db.flush()
    return record


# --------------------------------------------------------------------------- #
# 可售性校验（§14.4 不变量 1）——供 commerce 创建付费订单前调用
# --------------------------------------------------------------------------- #
async def assert_sellable(db: AsyncSession, product_id: uuid.UUID) -> bool:
    """无有效销售许可、确认价格或收益规则不能创建新付费订单（§14.4 不变量 1）。

    - 必须有双方确认（CONFIRMED/EFFECTIVE）的价格方案；
    - 必须有已发布的收益规则版本；
    - 若商品有冻结组成：每个组件都必须有生效中的销售许可，缺一个即拒绝，无部分绕过（§19 T10）；
    - 单品：确认价格所关联的协议版本必须生效。

    校验通过返回 True；不通过抛 Conflict（调用方 commerce 据此禁止下单）。
    """
    from app.modules.commerce.models import Product

    product = await db.get(Product, product_id)
    if product is None:
        raise NotFound("商品不存在")

    price = await db.scalar(
        select(PriceProposal)
        .where(
            PriceProposal.product_id == product_id,
            PriceProposal.state.in_(_CONFIRMED_PRICE_STATES),
        )
        .order_by(PriceProposal.version_no.desc())
        .limit(1)
    )
    if price is None or not price.final_fen or price.final_fen <= 0:
        raise Conflict("缺少双方确认的价格", code="PRICE_NOT_CONFIRMED")

    rule = await db.scalar(
        select(RevenueRuleVersion).order_by(RevenueRuleVersion.version_no.desc()).limit(1)
    )
    if rule is None:
        raise Conflict("缺少已发布的收益规则", code="REVENUE_RULE_MISSING")

    composition = await db.scalar(
        select(ProductCompositionVersion)
        .where(ProductCompositionVersion.product_id == product_id)
        .order_by(ProductCompositionVersion.version_no.desc())
        .limit(1)
    )
    if composition is not None:
        components = list(
            (
                await db.execute(
                    select(ProductComponent).where(
                        ProductComponent.composition_version_id == composition.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for component in components:
            if component.agreement_version_id is None:
                raise Conflict("组合中存在缺少销售许可的组件", code="LICENSE_NOT_ACTIVE")
            version = await db.get(AgreementVersion, component.agreement_version_id)
            if version is None or version.state != AgreementState.ACTIVE.value:
                raise Conflict("组合中存在缺少销售许可的组件", code="LICENSE_NOT_ACTIVE")
        return True

    # 单品：确认价格必须关联生效中的协议版本。
    if price.agreement_version_id is None:
        raise Conflict("缺少有效的销售许可", code="LICENSE_NOT_ACTIVE")
    version = await db.get(AgreementVersion, price.agreement_version_id)
    if version is None or version.state != AgreementState.ACTIVE.value:
        raise Conflict("缺少有效的销售许可", code="LICENSE_NOT_ACTIVE")
    return True
