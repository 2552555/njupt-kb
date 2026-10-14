"""creator 应用服务：贡献记录、贡献汇总与贡献者资料；并作为 P3 收益接口的对外入口。

设计文档 §24.1；契约见 docs/INTERFACES.md 的 `## creator`。

有效使用 ≠ 授权链接次数（§24.1）：自下载、重复刷取、短时异常与无权传播资料不得机械计为
收益贡献，正常学习中的重复访问也不当作作弊。`record_contribution` 只接受"事实型"贡献
（获审通过的原创/授权资料、修订、被采纳纠错、解决需求），并拒绝以访问/下载次数为引用。

P3 的账本、提现与结算数据由 commerce 拥有：这里通过函数体内局部导入调用 commerce 服务
（docs/ARCHITECTURE.md §2.3），不直接读写 commerce 的表。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ContributionKind
from app.core.errors import InvalidInput
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.modules.creator.models import Contribution, CreatorProfile
from app.modules.creator.schemas import (
    ContributionOut,
    ContributionSummary,
    CreatorLedgerEntryOut,
    CreatorProfileOut,
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
            display_name=profile.display_name,
            headline=profile.headline,
            bio=profile.bio,
            avatar_url=profile.avatar_url,
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
