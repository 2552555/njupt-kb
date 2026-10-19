"""creator 应用服务：贡献记录、贡献汇总与贡献者资料；并作为 P3 收益接口的对外入口。

设计文档 §24.1；契约见 docs/INTERFACES.md 的 `## creator`。
用户权责体系 §3.4、§14.1：专业贡献者入驻申请与审核结论（`contributor_applications`）。

有效使用 ≠ 授权链接次数（§24.1）：自下载、重复刷取、短时异常与无权传播资料不得机械计为
收益贡献，正常学习中的重复访问也不当作作弊。`record_contribution` 只接受"事实型"贡献
（获审通过的原创/授权资料、修订、被采纳纠错、解决需求），并拒绝以访问/下载次数为引用。

P3 的账本、提现与结算数据由 commerce 拥有：这里通过函数体内局部导入调用 commerce 服务
（docs/ARCHITECTURE.md §2.3），不直接读写 commerce 的表。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import (
    AccountStatus,
    ContributionKind,
    ContributorOnboardingStatus,
    DomainEventType,
    Role,
)
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound, RateLimited
from app.core.events import emit_event
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.core.ratelimit import check_rate_limit
from app.modules.creator.models import Contribution, ContributorApplication, CreatorProfile
from app.modules.creator.schemas import (
    ContributionOut,
    ContributionSummary,
    ContributorApplicationCreate,
    ContributorApplicationOut,
    CreatorLedgerEntryOut,
    CreatorProfileOut,
    CreatorSelfProfileOut,
    CreatorSettlementOut,
    CreatorWithdrawalOut,
    SettlementRunRequest,
    SettlementRunResult,
    WithdrawalRequest,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

# 以访问/下载等使用行为为引用的记录：不作为贡献计入（§24.1）。
USAGE_ONLY_REFERENCE_TYPES = frozenset(
    {"download", "download_grant", "preview_grant", "usage", "access", "view", "impression"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# 贡献记录（§24.1）
# --------------------------------------------------------------------------
async def record_contribution(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: ContributionKind | str,
    reference_type: str,
    reference_id: uuid.UUID,
    note: str | None = None,
) -> Contribution | None:
    """记录一条贡献；同一业务事实幂等，使用行为不机械计为贡献（§24.1）。

    返回 None 表示该记录被判定为"仅使用行为"，未计入贡献。
    """
    value = kind.value if isinstance(kind, ContributionKind) else str(kind)
    try:
        kind_enum = ContributionKind(value)
    except ValueError as exc:
        raise InvalidInput("未知的贡献类型", details={"kind": value}) from exc

    # 自下载、重复刷取、短时异常、无权传播资料等不得机械计为收益贡献（§24.1）。
    if reference_type in USAGE_ONLY_REFERENCE_TYPES:
        return None

    existing = (
        await db.execute(
            select(Contribution).where(
                Contribution.user_id == user_id,
                Contribution.kind == kind_enum.value,
                Contribution.reference_type == reference_type,
                Contribution.reference_id == reference_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    contribution = Contribution(
        user_id=user_id,
        kind=kind_enum.value,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        points=0,  # 贡献分公式待确认（D16），默认 0，不构成现金负债（§24.1、§5.10）
    )
    try:
        # 用保存点隔离唯一约束冲突，避免并发重复计入时回滚调用方事务。
        async with db.begin_nested():
            db.add(contribution)
            await db.flush()
    except IntegrityError:
        return (
            await db.execute(
                select(Contribution).where(
                    Contribution.user_id == user_id,
                    Contribution.kind == kind_enum.value,
                    Contribution.reference_type == reference_type,
                    Contribution.reference_id == reference_id,
                )
            )
        ).scalar_one_or_none()
    return contribution


async def contribution_summary(db: AsyncSession, user_id: uuid.UUID) -> ContributionSummary:
    """贡献汇总：按类型计数与展示用贡献分（不承诺兑换现金，§24.1）。"""
    rows = (
        await db.execute(
            select(Contribution.kind, func.count())
            .where(Contribution.user_id == user_id, Contribution.revoked_at.is_(None))
            .group_by(Contribution.kind)
        )
    ).all()
    by_kind = {str(kind): int(count) for kind, count in rows}
    total = sum(by_kind.values())
    points = await db.scalar(
        select(func.coalesce(func.sum(Contribution.points), 0)).where(
            Contribution.user_id == user_id, Contribution.revoked_at.is_(None)
        )
    )
    has_profile = (
        await db.execute(select(CreatorProfile.id).where(CreatorProfile.user_id == user_id))
    ).scalar_one_or_none() is not None
    return ContributionSummary(
        user_id=user_id,
        total=total,
        by_kind=by_kind,
        points=int(points or 0),
        is_creator=has_profile or total > 0,
    )


async def list_contributions(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[ContributionOut]:
    """我的贡献记录（只读，游标分页）。"""
    size = normalize_page_size(limit)
    stmt = (
        select(Contribution)
        .where(Contribution.user_id == user_id)
        .order_by(Contribution.created_at.desc(), Contribution.id.desc())
    )
    stmt = _apply_time_cursor(stmt, Contribution, cursor)
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = _cursor_of(items[-1]) if has_more and items else None
    return CursorPage[ContributionOut](
        items=[ContributionOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_creator_profile(db: AsyncSession, user_id: uuid.UUID) -> CreatorProfileOut:
    """贡献者公开资料（只读）。资料与普通账号分离；账号不存在抛 NotFound。"""
    from app.modules.identity.service import display_name_of, get_user

    user = await get_user(db, user_id)  # 找不到抛 NotFound
    profile = (
        await db.execute(select(CreatorProfile).where(CreatorProfile.user_id == user_id))
    ).scalar_one_or_none()
    summary = await contribution_summary(db, user_id)

    if profile is not None and profile.is_public:
        return CreatorProfileOut(
            user_id=user_id,
            display_name=display_name_of(user),
            headline=profile.headline,
            bio=profile.bio,
            # 昵称与头像始终以账号资料为准，避免两处长期不一致。
            avatar_url=getattr(getattr(user, "profile", None), "avatar_url", None),
            contribution=summary,
        )
    # 无资料或未公开：只回退到账号显示名与贡献汇总，不编造资料。
    return CreatorProfileOut(
        user_id=user_id,
        display_name=display_name_of(user),
        headline=None,
        bio=None,
        avatar_url=None,
        contribution=summary,
    )


async def get_own_creator_profile(db: AsyncSession, *, user: "User") -> CreatorSelfProfileOut:
    """本人视角：返回未公开的简介原文与是否公开，便于回填编辑表单。"""
    from app.modules.identity.service import display_name_of

    profile = (
        await db.execute(select(CreatorProfile).where(CreatorProfile.user_id == user.id))
    ).scalar_one_or_none()
    summary = await contribution_summary(db, user.id)
    account = getattr(user, "profile", None)
    return CreatorSelfProfileOut(
        user_id=user.id,
        display_name=display_name_of(user),
        headline=profile.headline if profile else None,
        bio=profile.bio if profile else None,
        avatar_url=getattr(account, "avatar_url", None),
        is_public=profile.is_public if profile else True,
        contribution=summary,
    )


async def update_creator_profile(
    db: AsyncSession, *, user: "User", payload: dict, request_id: str | None
) -> CreatorSelfProfileOut:
    """维护自己的贡献者资料；昵称与头像跟随账号资料，不在此重复维护（§24.1）。"""
    from app.modules.identity.service import display_name_of

    profile = (
        await db.execute(select(CreatorProfile).where(CreatorProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        # display_name 非空，建档时写入账号昵称占位；读取仍以账号资料为准。
        profile = CreatorProfile(user_id=user.id, display_name=display_name_of(user))
        db.add(profile)
        await db.flush()

    # headline/bio 显式传 null 表示清空；is_public 为布尔，缺席即不改。
    for field in ("headline", "bio"):
        if field in payload:
            setattr(profile, field, payload[field])
    if payload.get("is_public") is not None:
        profile.is_public = payload["is_public"]

    await record_audit(
        db,
        action="creator.profile_updated",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={"changed_fields": sorted(payload)},
        request_id=request_id,
    )
    return await get_own_creator_profile(db, user=user)


# --------------------------------------------------------------------------
# 贡献者入驻（用户权责 §3.4、§14.1、§16.2）
# --------------------------------------------------------------------------
# 未关闭（占用名额）的申请状态；终态为 active/rejected（suspended/exited 本版不实现）。
_OPEN_ONBOARDING_STATUSES = (
    ContributorOnboardingStatus.APPLIED,
    ContributorOnboardingStatus.UNDER_REVIEW,
)
# 状态机：applied → under_review → active/rejected。允许在正式受理前直接拒绝明显不合格的申请。
_ONBOARDING_TRANSITIONS: dict[
    ContributorOnboardingStatus, frozenset[ContributorOnboardingStatus]
] = {
    ContributorOnboardingStatus.APPLIED: frozenset(
        {ContributorOnboardingStatus.UNDER_REVIEW, ContributorOnboardingStatus.REJECTED}
    ),
    ContributorOnboardingStatus.UNDER_REVIEW: frozenset(
        {ContributorOnboardingStatus.ACTIVE, ContributorOnboardingStatus.REJECTED}
    ),
}


async def submit_application(
    db: AsyncSession,
    *,
    user: "User",
    payload: ContributorApplicationCreate,
    request_id: str,
) -> ContributorApplication:
    """提交专业贡献者入驻申请（登录即可，普通用户可直接投稿免费内容）。

    按用户维度限频并限制未关闭申请数，避免单一账号刷爆入驻审核队列（§3.4）。
    """
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能提交入驻申请")

    allowed = await check_rate_limit(
        f"contributor-application:{user.id}",
        limit=settings.CONTRIBUTOR_APPLICATION_RATE_LIMIT,
        window_seconds=settings.CONTRIBUTOR_APPLICATION_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise RateLimited("入驻申请提交过于频繁，请稍后再试")

    from app.modules.identity.models import User

    user = await db.scalar(
        select(User).where(User.id == user.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能提交入驻申请")

    # 已是贡献者或管理员：无需重复入驻。
    if user.role in (Role.CONTRIBUTOR, Role.ADMIN):
        raise Conflict("你已是贡献者或管理员，无需重复申请", code="ALREADY_ONBOARDED")
    active = await db.scalar(
        select(ContributorApplication.id).where(
            ContributorApplication.user_id == user.id,
            ContributorApplication.status.in_(["active", "suspended"]),
        )
    )
    if active is not None:
        raise Conflict("已有入驻身份，请在原记录处理恢复或退出", code="ALREADY_ONBOARDED")

    open_count = await db.scalar(
        select(func.count())
        .select_from(ContributorApplication)
        .where(
            ContributorApplication.user_id == user.id,
            ContributorApplication.status.in_([s.value for s in _OPEN_ONBOARDING_STATUSES]),
        )
    )
    if open_count >= settings.CONTRIBUTOR_APPLICATION_OPEN_CAP:
        raise Conflict(
            "你有未关闭的入驻申请，请等待处理后再提交",
            code="ONBOARDING_OPEN_CAP_EXCEEDED",
        )

    application = ContributorApplication(
        user_id=user.id,
        status=ContributorOnboardingStatus.APPLIED.value,
        capability_summary=payload.capability_summary,
        rights_materials_ref=payload.rights_materials_ref,
        verification_ref=payload.verification_ref,
        contract_party_ref=payload.contract_party_ref,
        submitted_at=_now(),
    )
    db.add(application)
    await db.flush()

    await record_audit(
        db,
        action="contributor_application.submitted",
        object_type="contributor_application",
        object_id=application.id,
        actor_id=user.id,
        after={
            "status": ContributorOnboardingStatus.APPLIED.value,
            "user_id": str(user.id),
        },
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.CONTRIBUTOR_APPLICATION_SUBMITTED,
        subject_type="contributor_application",
        subject_id=application.id,
        payload={
            "application_id": str(application.id),
            "user_id": str(user.id),
            "status": ContributorOnboardingStatus.APPLIED.value,
        },
        dedupe_key=f"contributor-application-submitted:{application.id}",
    )
    return application


async def decide_application(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    actor: "User",
    decision: str,
    reason: str,
    request_id: str,
) -> ContributorApplication:
    """审核入驻申请：受理、通过或拒绝（§3.4、§14.1）。

    `active` 时把账号角色从 `user` 提升为 `contributor`（已是 contributor/admin 不变）。
    拒绝须说明理由并允许补充（重新申请即可）。本版不实现降级/退出。
    """
    application = await db.get(ContributorApplication, application_id)
    if application is None:
        raise NotFound("入驻申请不存在")

    reason = (reason or "").strip()
    if not reason or len(reason) > 255:
        raise InvalidInput("审核理由须为 1–255 字")

    try:
        target = ContributorOnboardingStatus(str(decision))
    except ValueError as exc:
        raise InvalidInput("审核决定无效", details={"decision": str(decision)}) from exc
    if target not in (
        ContributorOnboardingStatus.UNDER_REVIEW,
        ContributorOnboardingStatus.ACTIVE,
        ContributorOnboardingStatus.REJECTED,
    ):
        # 降级/退出（suspended/exited）本版不实现，不作为可下达的审核决定（§3.4）。
        raise InvalidInput("审核决定无效", details={"decision": str(decision)})

    current = ContributorOnboardingStatus(application.status)
    if target not in _ONBOARDING_TRANSITIONS.get(current, frozenset()):
        raise Conflict(
            "不允许的入驻状态流转",
            code="INVALID_ONBOARDING_TRANSITION",
            details={"from": current.value, "to": target.value},
        )

    now = _now()
    terminal = target in (
        ContributorOnboardingStatus.ACTIVE,
        ContributorOnboardingStatus.REJECTED,
    )
    # 条件更新：状态被并发处理改变时只有一个生效（§29）。
    result = await db.execute(
        update(ContributorApplication)
        .where(
            ContributorApplication.id == application_id,
            ContributorApplication.status == current.value,
        )
        .values(
            status=target.value,
            decided_at=now,
            decided_by=actor.id,
            decision_reason=reason,
            closed_at=now if terminal else None,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise Conflict("入驻申请状态已变化，请刷新后重试", code="ONBOARDING_STATE_CHANGED")
    await db.refresh(application)

    role_after: str | None = None
    if target == ContributorOnboardingStatus.ACTIVE:
        role_after = await _promote_to_contributor(db, application.user_id)

    await record_audit(
        db,
        action="contributor_application.decided",
        object_type="contributor_application",
        object_id=application_id,
        actor_id=actor.id,
        before={"status": current.value},
        after={
            "status": target.value,
            "decision": target.value,
            "user_id": str(application.user_id),
            "role": role_after,
        },
        reason=reason,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.CONTRIBUTOR_APPLICATION_DECIDED,
        subject_type="contributor_application",
        subject_id=application_id,
        payload={
            "application_id": str(application_id),
            "user_id": str(application.user_id),
            "status": target.value,
            "decision": target.value,
            "reason": reason,
            "decided_by": str(actor.id),
        },
        dedupe_key=f"contributor-application-decided:{application_id}:{target.value}",
    )
    return application


async def _promote_to_contributor(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """入驻通过时把普通用户提升为贡献者；已是 contributor/admin 则不变。

    identity 未提供改角色的服务函数，故在此按显式契约直接更新 `users.role`
    （仅 user→contributor 这一种提升，不降级、不触碰其他角色）。
    """
    from app.modules.identity.models import User

    user = await db.get(User, user_id)
    if user is None:
        return None
    if user.role == Role.USER:
        user.role = Role.CONTRIBUTOR.value
        await db.flush()
    return str(user.role)


async def change_contributor_status(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    actor: "User",
    expected_status: str,
    status: str,
    reason: str,
    request_id: str,
    self_exit: bool = False,
) -> ContributorApplication:
    """暂停、恢复或退出专业身份；合同、收益及本人内容归属独立保留。"""
    from app.core.permissions import CONTRIBUTOR_REVIEW, effective_permissions
    from app.modules.assignment.service import revoke_resource_assignment
    from app.modules.assignment.models import ResourceAssignment
    from app.modules.identity.models import User

    application = await db.get(ContributorApplication, application_id)
    if application is None or (self_exit and application.user_id != actor.id):
        raise NotFound("入驻记录不存在")
    if self_exit:
        if status != ContributorOnboardingStatus.EXITED:
            raise Forbidden("本人仅可申请退出专业身份")
    elif CONTRIBUTOR_REVIEW not in await effective_permissions(db, actor):
        raise Forbidden("需要入驻管理权限")
    reason = reason.strip()
    if not reason or len(reason) > 255:
        raise InvalidInput("变更理由须为 1–255 字")
    allowed = {
        "active": {"suspended", "exited"},
        "suspended": {"active", "exited"},
    }
    if status not in allowed.get(expected_status, set()):
        raise Conflict("不允许的专业身份流转", code="INVALID_ONBOARDING_TRANSITION")

    # 与入驻申请共用用户行锁，防止退出和重新申请交错生成两份有效身份。
    target_user = await db.scalar(
        select(User).where(User.id == application.user_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if target_user is None or target_user.status != AccountStatus.ACTIVE:
        raise Conflict("账号当前不可变更专业身份")
    now = _now()
    result = await db.execute(
        update(ContributorApplication)
        .where(ContributorApplication.id == application_id,
               ContributorApplication.status == expected_status)
        .values(status=status, decided_at=now, decided_by=actor.id,
                decision_reason=reason, closed_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise Conflict("专业身份已变化，请刷新重试", code="ONBOARDING_STATE_CHANGED")
    await db.refresh(application)

    if status == ContributorOnboardingStatus.ACTIVE:
        await _promote_to_contributor(db, target_user.id)
    else:
        if target_user.role == Role.CONTRIBUTOR:
            target_user.role = Role.USER.value
        assignments = list((await db.scalars(
            select(ResourceAssignment).where(
                ResourceAssignment.user_id == target_user.id,
                ResourceAssignment.revoked_at.is_(None),
            ).order_by(ResourceAssignment.id)
        )).all())
        for assignment in assignments:
            await revoke_resource_assignment(
                db, actor=actor, assignment_id=assignment.id, reason=reason,
                request_id=request_id,
            )
    await record_audit(
        db, action="contributor.status_changed", object_type="contributor_application",
        object_id=application_id, actor_id=actor.id,
        before={"status": expected_status}, after={"status": status},
        reason=reason, request_id=request_id,
    )
    await emit_event(
        db, event_type=DomainEventType.CONTRIBUTOR_APPLICATION_DECIDED,
        subject_type="contributor_application", subject_id=application_id,
        payload={"application_id": str(application_id), "user_id": str(target_user.id),
                 "status": status, "decision": status, "decided_by": str(actor.id)},
        dedupe_key=f"contributor-status:{application_id}:{uuid.uuid4()}",
    )
    return application


async def onboarding_status(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    """供其他模块查询某用户最近一次入驻申请状态；从未申请返回 None。"""
    return await db.scalar(
        select(ContributorApplication.status)
        .where(ContributorApplication.user_id == user_id)
        .order_by(
            ContributorApplication.created_at.desc(), ContributorApplication.id.desc()
        )
        .limit(1)
    )


async def list_my_applications(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[ContributorApplicationOut]:
    """本人入驻申请（只读，游标分页）。"""
    stmt = select(ContributorApplication).where(ContributorApplication.user_id == user_id)
    return await _application_page(db, stmt, limit=limit, cursor=cursor)


async def list_applications(
    db: AsyncSession,
    *,
    statuses: Sequence[ContributorOnboardingStatus] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[ContributorApplicationOut]:
    """后台入驻申请队列（可按状态过滤，游标分页）。"""
    stmt = select(ContributorApplication)
    if statuses:
        stmt = stmt.where(ContributorApplication.status.in_([str(s) for s in statuses]))
    return await _application_page(db, stmt, limit=limit, cursor=cursor)


async def _application_page(
    db: AsyncSession, stmt: Any, *, limit: int | None, cursor: str | None
) -> CursorPage[ContributorApplicationOut]:
    size = normalize_page_size(limit)
    stmt = _apply_time_cursor(stmt, ContributorApplication, cursor)
    stmt = stmt.order_by(
        ContributorApplication.created_at.desc(), ContributorApplication.id.desc()
    )
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = _cursor_of(items[-1]) if has_more and items else None
    return CursorPage[ContributorApplicationOut](
        items=[ContributorApplicationOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------
# P3 收益接口：委托 commerce（函数体内局部导入）
# --------------------------------------------------------------------------
async def list_ledger(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[CreatorLedgerEntryOut]:
    """我的账本分录（只读）；数据由 commerce 提供。"""
    from app.modules.commerce.service import list_ledger_entries

    page = await list_ledger_entries(db, user_id=user_id, limit=limit, cursor=cursor)
    return CursorPage[CreatorLedgerEntryOut](
        items=[CreatorLedgerEntryOut.model_validate(item.model_dump()) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


async def request_withdrawal(
    db: AsyncSession,
    *,
    user: "User",
    payload: WithdrawalRequest,
    request_id: str,
    idempotency_key: str | None,
) -> CreatorWithdrawalOut:
    """申请提现；最低提现额未配置或商业未启用时由 commerce 抛 NotEnabled（§24.3、§25.2）。"""
    from app.modules.commerce.schemas import WithdrawalCreate
    from app.modules.commerce.service import create_withdrawal

    result = await create_withdrawal(
        db,
        user=user,
        payload=WithdrawalCreate(amount=payload.amount, currency=payload.currency),
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    return CreatorWithdrawalOut.model_validate(result.model_dump())


async def list_withdrawals(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int | None, cursor: str | None
) -> CursorPage[CreatorWithdrawalOut]:
    """我的提现记录（只读）；数据由 commerce 提供。"""
    from app.modules.commerce.service import list_withdrawals as _list_withdrawals

    page = await _list_withdrawals(db, user_id=user_id, limit=limit, cursor=cursor)
    return CursorPage[CreatorWithdrawalOut](
        items=[CreatorWithdrawalOut.model_validate(item.model_dump()) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


async def run_settlement(
    db: AsyncSession, *, actor: "User", payload: SettlementRunRequest, request_id: str
) -> SettlementRunResult:
    """按已批准规则计算某周期结算；规则未配置或商业未启用时由 commerce 抛 NotEnabled。"""
    from app.modules.commerce.schemas import SettlementRunIn
    from app.modules.commerce.service import run_settlement as _run_settlement

    result = await _run_settlement(
        db,
        actor=actor,
        payload=SettlementRunIn(
            period_start=payload.period_start,
            period_end=payload.period_end,
            currency=payload.currency,
        ),
        request_id=request_id,
    )
    return SettlementRunResult(
        created=result.created,
        settlements=[
            CreatorSettlementOut.model_validate(item.model_dump())
            for item in result.settlements
        ],
    )


# --------------------------------------------------------------------------
# 游标辅助（§25.1）
# --------------------------------------------------------------------------
def _cursor_of(obj: Any) -> str:
    created_at = obj.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return encode_cursor({"t": created_at.isoformat(), "i": str(obj.id)})


def _apply_time_cursor(stmt: Any, model: Any, cursor: str | None) -> Any:
    if not cursor:
        return stmt
    data = decode_cursor(cursor)
    try:
        ts = datetime.fromisoformat(str(data["t"]))
        cid = uuid.UUID(str(data["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return stmt.where(
        or_(model.created_at < ts, and_(model.created_at == ts, model.id < cid))
    )
