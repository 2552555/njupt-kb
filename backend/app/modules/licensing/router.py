"""licensing 路由（用户权责体系 §16.2、§16.3）。

只做参数解析、权限依赖与调用 `service`；业务规则不在这里。
前缀由 `app/main.py` 统一加 `/api/v1`，本模块不自行决定前缀（docs/ARCHITECTURE.md §1）。

开关关闭（`COMMERCE_ENABLED=false` 或 `COMMERCE_COMPLIANCE_GATE_CLOSED=true`）时：
- 写接口（协议确认、报价、后台创建）由 `service` 抛 `NotEnabled` → HTTP 503 / `FEATURE_NOT_ENABLED`；
- 只读接口（`/me/agreements`、后台查询）不涉及资金，仍正常工作。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.pagination import CursorPage
from app.core.permissions import AGREEMENT_MANAGE, PRICE_CONFIRM, require
from app.modules.licensing import service
from app.modules.licensing.schemas import (
    AgreementAcceptanceIn,
    AgreementAcceptanceOut,
    AgreementCreateIn,
    AgreementDetailOut,
    AgreementOut,
    AgreementVersionCreateIn,
    AgreementVersionOut,
    CompositionVersionCreateIn,
    CompositionVersionOut,
    PriceAcceptanceIn,
    PriceProposalCreateIn,
    PriceProposalOut,
    PriceRejectionIn,
    RevenueRuleVersionCreateIn,
    RevenueRuleVersionOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["licensing"])


# --------------------------------------------------------------------------- #
# 用户/贡献者侧
# --------------------------------------------------------------------------- #
@router.get("/me/agreements", response_model=list[AgreementDetailOut])
async def list_my_agreements(
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgreementDetailOut]:
    """本人协议及历史版本（只读）。不返回他人商业条款，也不含任何资金字段。"""
    return await service.list_my_agreements(db, user=user)


@router.post(
    "/agreements/{version_id}/acceptances",
    response_model=AgreementAcceptanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def accept_agreement(
    version_id: uuid.UUID,
    payload: AgreementAcceptanceIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgreementAcceptanceOut:
    """确认特定协议全文及附件哈希（主体签约权限由服务端判定）。"""
    return await service.accept_agreement(
        db,
        user=user,
        version_id=version_id,
        payload=payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/products/{product_id}/price-proposals",
    response_model=PriceProposalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_price_proposal(
    product_id: uuid.UUID,
    payload: PriceProposalCreateIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PriceProposalOut:
    """对应权利方提出建议售价；改价重建版本，不覆盖旧版。"""
    return await service.create_price_proposal(
        db,
        user=user,
        product_id=product_id,
        payload=payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/price-proposals/{proposal_id}/acceptances",
    response_model=PriceProposalOut,
    status_code=status.HTTP_201_CREATED,
)
async def accept_price_proposal(
    proposal_id: uuid.UUID,
    payload: PriceAcceptanceIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PriceProposalOut:
    """双方确认同一价格版本；确认方身份由服务端按会话与签约授权判定。"""
    return await service.accept_price_proposal(
        db,
        user=user,
        proposal_id=proposal_id,
        payload=payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/price-proposals/{proposal_id}/rejections",
    response_model=PriceProposalOut,
)
async def reject_price_proposal(
    proposal_id: uuid.UUID,
    payload: PriceRejectionIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> PriceProposalOut:
    """否决价格版本（beta16）：权利方本人或持 `PRICE_CONFIRM` 的平台人员。

    否决使该版本不可再确认，但不覆盖历史金额（不变量 3）；「否决」与「提案方撤回」
    共用 `WITHDRAWN` 终态，差别由审计与 `price.rejected` 域事件区分。
    """
    return await service.reject_price_proposal(
        db,
        user=user,
        proposal_id=proposal_id,
        reason=payload.reason,
        request_id=request_id,
    )


# --------------------------------------------------------------------------- #
# 后台侧（均需 require(AGREEMENT_MANAGE)，价格确认用 require(PRICE_CONFIRM)）
# --------------------------------------------------------------------------- #
@router.get("/admin/agreements", response_model=CursorPage[AgreementOut])
async def list_admin_agreements(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    actor: "User" = Depends(require(AGREEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[AgreementOut]:
    """协议列表（只读，分页）。"""
    return await service.list_admin_agreements(db, limit=limit, cursor=cursor)


@router.post(
    "/admin/agreements", response_model=AgreementOut, status_code=status.HTTP_201_CREATED
)
async def create_agreement(
    payload: AgreementCreateIn,
    actor: "User" = Depends(require(AGREEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> AgreementOut:
    """创建协议主体。"""
    return await service.create_agreement(db, actor=actor, payload=payload, request_id=request_id)


@router.post(
    "/admin/agreements/{agreement_id}/versions",
    response_model=AgreementVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_agreement_version(
    agreement_id: uuid.UUID,
    payload: AgreementVersionCreateIn,
    actor: "User" = Depends(require(AGREEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> AgreementVersionOut:
    """生成协议新版本，不覆盖旧版。"""
    return await service.create_agreement_version(
        db, actor=actor, agreement_id=agreement_id, payload=payload, request_id=request_id
    )


@router.post(
    "/admin/price-proposals/{proposal_id}/acceptances",
    response_model=PriceProposalOut,
    status_code=status.HTTP_201_CREATED,
)
async def admin_accept_price_proposal(
    proposal_id: uuid.UUID,
    payload: PriceAcceptanceIn,
    actor: "User" = Depends(require(PRICE_CONFIRM)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PriceProposalOut:
    """平台确认价格；不可代表贡献者确认。"""
    return await service.admin_accept_price_proposal(
        db,
        actor=actor,
        proposal_id=proposal_id,
        payload=payload,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/admin/revenue-rule-versions",
    response_model=RevenueRuleVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def publish_revenue_rule(
    payload: RevenueRuleVersionCreateIn,
    actor: "User" = Depends(require(AGREEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> RevenueRuleVersionOut:
    """发布收益规则版本；`creator_bps` 必须显式给出，无默认比例；发布后不可改。"""
    return await service.publish_revenue_rule(
        db, actor=actor, payload=payload, request_id=request_id
    )


@router.post(
    "/admin/product-composition-versions",
    response_model=CompositionVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def freeze_composition(
    payload: CompositionVersionCreateIn,
    actor: "User" = Depends(require(AGREEMENT_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CompositionVersionOut:
    """冻结商品组成；权重非负且至少一项正数。"""
    return await service.freeze_composition(
        db, actor=actor, payload=payload, request_id=request_id
    )


@router.get("/admin/price-proposals", response_model=CursorPage[PriceProposalOut])
async def list_admin_price_proposals(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    actor: "User" = Depends(require(PRICE_CONFIRM)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[PriceProposalOut]:
    """价格方案列表（只读，分页）。"""
    return await service.list_admin_price_proposals(db, limit=limit, cursor=cursor)


@router.get("/me/price-proposals", response_model=CursorPage[PriceProposalOut])
async def list_my_price_proposals(
    limit: int | None = Query(default=None, ge=1, le=100), cursor: str | None = None,
    user: "User" = Depends(get_current_user), db: AsyncSession = Depends(get_db),
) -> CursorPage[PriceProposalOut]:
    return await service.list_admin_price_proposals(db, owner_id=user.id, limit=limit, cursor=cursor)
