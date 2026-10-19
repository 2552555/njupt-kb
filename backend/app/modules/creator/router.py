"""creator 路由：只做参数解析与调用，业务规则在 service.py。

前缀由 app/main.py 统一加 /api/v1，模块不自行决定前缀（docs/ARCHITECTURE.md §1）。
只读接口（贡献者资料、我的贡献、我的账本）不涉及资金，任何阶段都可用；
写接口（提现、结算）在 `COMMERCE_ENABLED=false` 或规则未配置时抛 NotEnabled（§25.2）。
贡献者入驻（§3.4）：普通用户登录即可申请；后台审批需 `contributor.review`。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.enums import ContributorOnboardingStatus
from app.core.pagination import CursorPage
from app.core.permissions import CONTRIBUTOR_REVIEW, SETTLEMENT_MANAGE, require
from app.modules.creator import service
from app.modules.creator.schemas import (
    ContributionOut,
    ContributorApplicationCreate,
    ContributorApplicationDecisionIn,
    ContributorApplicationOut,
    ContributorLifecycleIn,
    CreatorLedgerEntryOut,
    CreatorProfileOut,
    CreatorProfileUpdate,
    CreatorSelfProfileOut,
    CreatorWithdrawalOut,
    SettlementRunRequest,
    SettlementRunResult,
    WithdrawalRequest,
)

router = APIRouter(tags=["creator"])


@router.get("/creators/{user_id}", response_model=CreatorProfileOut)
async def get_creator_profile(
    user_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CreatorProfileOut:
    """贡献者公开资料（只读）。"""
    return await service.get_creator_profile(db, user_id)


@router.get("/me/creator-profile", response_model=CreatorSelfProfileOut)
async def get_my_creator_profile(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreatorSelfProfileOut:
    """本人视角的贡献者资料：包含是否公开与未公开的简介原文。"""
    return await service.get_own_creator_profile(db, user=user)


@router.put("/me/creator-profile", response_model=CreatorSelfProfileOut)
async def update_my_creator_profile(
    body: CreatorProfileUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> CreatorSelfProfileOut:
    """维护自己的贡献者资料（简介、一句话介绍、是否公开）；不改变账号权限。"""
    return await service.update_creator_profile(
        db, user=user, payload=body.model_dump(exclude_unset=True), request_id=request_id
    )


@router.post(
    "/me/contributor-applications",
    response_model=ContributorApplicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_contributor_application(
    body: ContributorApplicationCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ContributorApplicationOut:
    """提交专业贡献者入驻申请（登录即可，普通用户可直接投稿免费内容）。"""
    application = await service.submit_application(
        db, user=user, payload=body, request_id=request_id
    )
    return ContributorApplicationOut.model_validate(application)


@router.get(
    "/me/contributor-applications", response_model=CursorPage[ContributorApplicationOut]
)
async def list_my_contributor_applications(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ContributorApplicationOut]:
    """本人入驻申请（只读，游标分页）。"""
    return await service.list_my_applications(
        db, user_id=user.id, limit=limit, cursor=cursor
    )


@router.get(
    "/admin/contributor-applications", response_model=CursorPage[ContributorApplicationOut]
)
async def list_contributor_applications(
    application_status: list[ContributorOnboardingStatus] | None = Query(
        default=None, alias="status"
    ),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(require(CONTRIBUTOR_REVIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ContributorApplicationOut]:
    """后台入驻申请队列（需 contributor.review，可按状态过滤）。"""
    return await service.list_applications(
        db, statuses=application_status, limit=limit, cursor=cursor
    )


@router.post(
    "/admin/contributor-applications/{application_id}/decisions",
    response_model=ContributorApplicationOut,
    status_code=status.HTTP_201_CREATED,
)
async def decide_contributor_application(
    application_id: uuid.UUID,
    body: ContributorApplicationDecisionIn,
    user=Depends(require(CONTRIBUTOR_REVIEW)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ContributorApplicationOut:
    """审核入驻申请：受理（under_review）、通过（active）、拒绝（rejected）。"""
    application = await service.decide_application(
        db,
        application_id=application_id,
        actor=user,
        decision=body.decision,
        reason=body.reason,
        request_id=request_id,
    )
    return ContributorApplicationOut.model_validate(application)


@router.post("/admin/contributor-applications/{application_id}/status",
             response_model=ContributorApplicationOut)
async def change_contributor_status(
    application_id: uuid.UUID, body: ContributorLifecycleIn,
    user=Depends(require(CONTRIBUTOR_REVIEW)), db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ContributorApplicationOut:
    result = await service.change_contributor_status(
        db, application_id=application_id, actor=user, **body.model_dump(),
        request_id=request_id,
    )
    return ContributorApplicationOut.model_validate(result)


@router.post("/me/contributor-applications/{application_id}/exit",
             response_model=ContributorApplicationOut)
async def exit_contributor(
    application_id: uuid.UUID, body: ContributorLifecycleIn,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ContributorApplicationOut:
    result = await service.change_contributor_status(
        db, application_id=application_id, actor=user, **body.model_dump(),
        request_id=request_id, self_exit=True,
    )
    return ContributorApplicationOut.model_validate(result)


@router.get("/me/contributions", response_model=CursorPage[ContributionOut])
async def list_my_contributions(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ContributionOut]:
    """我的贡献记录（只读，游标分页）。"""
    return await service.list_contributions(db, user_id=user.id, limit=limit, cursor=cursor)


@router.get("/me/ledger", response_model=CursorPage[CreatorLedgerEntryOut])
async def list_my_ledger(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CreatorLedgerEntryOut]:
    """我的账本分录（只读）。"""
    return await service.list_ledger(db, user_id=user.id, limit=limit, cursor=cursor)


@router.post(
    "/me/withdrawals", response_model=CreatorWithdrawalOut, status_code=status.HTTP_201_CREATED
)
async def create_my_withdrawal(
    body: WithdrawalRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CreatorWithdrawalOut:
    return await service.request_withdrawal(
        db,
        user=user,
        payload=body,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.get("/me/withdrawals", response_model=CursorPage[CreatorWithdrawalOut])
async def list_my_withdrawals(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CreatorWithdrawalOut]:
    """我的提现记录（只读）。"""
    return await service.list_withdrawals(db, user_id=user.id, limit=limit, cursor=cursor)


@router.post("/admin/settlements/run", response_model=SettlementRunResult)
async def run_settlement(
    body: SettlementRunRequest,
    actor=Depends(require(SETTLEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SettlementRunResult:
    """按已批准规则计算某周期结算（需 settlement.manage）；规则未配置时抛 NotEnabled。"""
    return await service.run_settlement(
        db, actor=actor, payload=body, request_id=request_id
    )
