"""案件、侵权通知与反通知的应用服务（设计文档 §13、§14.1、§15.5、§16.2、§16.3）。

业务规则的唯一落点。`router.py` 只做解析与调用。

跨模块协作（docs/ARCHITECTURE.md §2）：
- 表引用只写字符串外键，不导入对方模型类。
- 跨模块服务调用与模型导入一律在函数体内局部导入，避免模块顶层循环导入。
- 措施的实际效果只改对方拥有的关键列（直接更新行或调用其服务函数），不修改对方文件。

三类案件结论互不自动替代：内容下架不自动等于收入没收（§13.1、§13.4）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import (
    AccountStatus,
    CaseEventType,
    CaseMeasureKind,
    CaseMeasureStatus,
    CaseStatus,
    CaseSubjectType,
    CaseType,
    DomainEventType,
    GrantStatus,
    PublicationStatus,
    RightsNoticeStatus,
)
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound, RateLimited
from app.core.events import emit_event
from app.core.pagination import (
    CursorPage,
    encode_cursor,
    normalize_page_size,
    seek_descending,
)
from app.core.permissions import CASE_HANDLE, RIGHTS_NOTICE_HANDLE, effective_permissions
from app.core.ratelimit import check_rate_limit
from app.modules.cases.models import (
    Case,
    CaseEvent,
    CaseMeasure,
    CounterStatement,
    RightsNotice,
)
from app.modules.cases.schemas import (
    CaseAdminOut,
    CaseDetailOut,
    CaseEventOut,
    CaseListItemOut,
    CounterStatementOut,
    MeasureOut,
    RightsNoticeReceiptOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

__all__ = [
    "create_case",
    "list_my_cases",
    "get_my_case",
    "create_rights_notice",
    "create_counter_statement",
    "list_admin_cases",
    "append_case_event",
    "take_measure",
    "release_measure",
    "active_measures",
]

# 用户可自行发起的案件类型；RIGHTS_NOTICE 必须走专用入口（§16.2）。
CLAIMABLE_CASE_TYPES = (
    CaseType.DELIVERY_DISPUTE,
    CaseType.REVENUE_DISPUTE,
    CaseType.GENERAL_APPEAL,
)

# 后台事件流转允许的类型。
ADMIN_EVENT_TYPES = (
    CaseEventType.TRIAGED,
    CaseEventType.FORWARDED,
    CaseEventType.DECIDED,
    CaseEventType.CLOSED,
    CaseEventType.NOTE,
)

# 商业化关闭：停售/冻结收益只记录措施行，不改动任何资金（§13.4）。
COMMERCE_MEASURES = (CaseMeasureKind.BLOCK_SALES, CaseMeasureKind.FREEZE_REVENUE)

# 「活跃」授权状态：撤权只改状态、不删凭证记录（沿用 beta13 已确立的语义）。
_ACTIVE_GRANT_STATUSES = (GrantStatus.ISSUED.value, GrantStatus.CONSUMED.value)

# 未关闭案件判定：除 CLOSED 外的所有状态都计入上限（§14.1）。
_OPEN_STATES = tuple(s.value for s in CaseStatus if s != CaseStatus.CLOSED)

# 案件事件 -> 侵权通知状态（§13.3 状态联动）。
# 通知枚举没有「处理中」字面值：`FORWARDED`（已转送、进入处理）是唯一的进行中状态，
# 因此只在案件实际转送时推进；受理（TRIAGED）不等于转送，不移动通知状态。
_CASE_EVENT_NOTICE_STATUS = {
    CaseEventType.FORWARDED.value: RightsNoticeStatus.FORWARDED,
    CaseEventType.DECIDED.value: RightsNoticeStatus.MEASURED,
    CaseEventType.CLOSED.value: RightsNoticeStatus.CLOSED,
}

# 通知状态的前进次序：只前进不回退，已是终态不覆盖（幂等）。
_RIGHTS_NOTICE_ORDER = {
    RightsNoticeStatus.RECEIVED.value: 0,
    RightsNoticeStatus.NEEDS_INFO.value: 1,
    RightsNoticeStatus.FORWARDED.value: 2,
    RightsNoticeStatus.COUNTER_RECEIVED.value: 3,
    RightsNoticeStatus.MEASURED.value: 4,
    RightsNoticeStatus.CLOSED.value: 5,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 分页
# --------------------------------------------------------------------------- #


async def _page(db, stmt, *, model, limit, cursor):
    """按 (created_at, id) 降序的游标分页，默认 20、上限 100（§25.1）。"""
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
# 「可主张对象」校验：申请人必须与对象有实际关系（§16.2）
# --------------------------------------------------------------------------- #


async def _is_reporter(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    resource_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    comment_id: uuid.UUID | None = None,
) -> bool:
    """申请人是否就同一对象提交过举报（举报人视角）。"""
    from app.modules.moderation.models import Report

    conds = [Report.reporter_id == user_id]
    if resource_id is not None:
        conds.append(Report.resource_id == resource_id)
    if version_id is not None:
        conds.append(Report.version_id == version_id)
    if comment_id is not None:
        conds.append(Report.comment_id == comment_id)
    return await db.scalar(select(Report.id).where(*conds).limit(1)) is not None


async def _resource_owner_id(db: AsyncSession, resource_id: uuid.UUID) -> uuid.UUID | None:
    from app.modules.content.models import Resource

    return await db.scalar(select(Resource.owner_id).where(Resource.id == resource_id))


async def assert_claimable(
    db: AsyncSession,
    *,
    user: "User",
    subject_type: CaseSubjectType,
    subject_id: uuid.UUID,
) -> None:
    """申请人必须与该对象有实际关系，否则 Forbidden（§16.2）。"""
    kind = str(subject_type)

    if kind == CaseSubjectType.RESOURCE.value:
        owner = await _resource_owner_id(db, subject_id)
        if owner is None:
            raise NotFound("目标资源不存在")
        if owner == user.id:
            return
        if await _is_reporter(db, user_id=user.id, resource_id=subject_id):
            return
        raise Forbidden("你不是该资源的权利人，不能对其主张")

    if kind == CaseSubjectType.RESOURCE_VERSION.value:
        from app.modules.content.models import ResourceVersion

        version = await db.get(ResourceVersion, subject_id)
        if version is None:
            raise NotFound("目标版本不存在")
        if await _resource_owner_id(db, version.resource_id) == user.id:
            return
        if await _is_reporter(db, user_id=user.id, version_id=subject_id):
            return
        raise Forbidden("你不是该版本的权利人，不能对其主张")

    if kind == CaseSubjectType.COMMENT.value:
        from app.modules.interaction.models import Comment

        comment = await db.get(Comment, subject_id)
        if comment is None:
            raise NotFound("目标评论不存在")
        if comment.user_id == user.id:
            return
        if await _is_reporter(db, user_id=user.id, comment_id=subject_id):
            return
        raise Forbidden("你不是该评论的作者或举报人，不能对其主张")

    if kind == CaseSubjectType.ORDER.value:
        from app.modules.commerce.models import Order

        order = await db.get(Order, subject_id)
        if order is None:
            raise NotFound("目标订单不存在")
        if order.user_id == user.id:
            return
        raise Forbidden("你不是该订单的购买者，不能对其主张")

    if kind == CaseSubjectType.ORDER_ITEM.value:
        from app.modules.commerce.models import Order, OrderItem

        item = await db.get(OrderItem, subject_id)
        if item is None:
            raise NotFound("目标订单明细不存在")
        order = await db.get(Order, item.order_id)
        if order is not None and order.user_id == user.id:
            return
        raise Forbidden("你不是该订单明细的购买者，不能对其主张")

    # OTHER：对象不是平台可归属的具体业务对象（如账号级申诉），无法在此校验关系，
    # 依赖限频与未关闭上限约束滥用。
    return


# --------------------------------------------------------------------------- #
# 用户侧：创建与查询案件
# --------------------------------------------------------------------------- #


async def create_case(
    db: AsyncSession,
    *,
    applicant: "User",
    payload,
    request_id: str,
) -> Case:
    """创建案件（限 DELIVERY_DISPUTE / REVENUE_DISPUTE / GENERAL_APPEAL，§16.2）。"""
    if applicant.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能发起案件")
    if payload.case_type == CaseType.RIGHTS_NOTICE:
        raise InvalidInput("侵权通知请使用专用入口提交", code="RIGHTS_NOTICE_SPECIAL_ENTRY")
    if payload.case_type not in CLAIMABLE_CASE_TYPES:
        raise InvalidInput("不支持的案件类型")

    await assert_claimable(
        db,
        user=applicant,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
    )

    allowed = await check_rate_limit(
        f"case:{applicant.id}",
        limit=settings.CASE_RATE_LIMIT,
        window_seconds=settings.CASE_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise RateLimited("案件提交过于频繁，请稍后再试")

    open_count = await db.scalar(
        select(func.count())
        .select_from(Case)
        .where(Case.applicant_id == applicant.id, Case.state.in_(_OPEN_STATES))
    )
    if open_count >= settings.CASE_OPEN_CAP:
        raise Conflict(
            "你未关闭的案件数量已达上限，请等待处理后再提交",
            code="CASE_OPEN_CAP_EXCEEDED",
        )

    case = Case(
        case_type=str(payload.case_type),
        subject_type=str(payload.subject_type),
        subject_id=payload.subject_id,
        applicant_id=applicant.id,
        state=CaseStatus.OPEN.value,
        rule_version=payload.rule_version,
        title=payload.title,
        description=payload.description,
    )
    db.add(case)
    await db.flush()

    db.add(
        CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.SUBMITTED.value,
            actor_id=applicant.id,
        )
    )

    await emit_event(
        db,
        event_type=DomainEventType.CASE_CREATED,
        subject_type="case",
        subject_id=case.id,
        payload={
            "case_type": str(payload.case_type),
            "subject_type": str(payload.subject_type),
            "subject_id": str(payload.subject_id),
            "applicant_id": str(applicant.id),
        },
        dedupe_key=f"case-created:{case.id}",
    )
    await record_audit(
        db,
        action="case.created",
        object_type="case",
        object_id=case.id,
        actor_id=applicant.id,
        after={
            "case_type": str(payload.case_type),
            "subject_type": str(payload.subject_type),
            "subject_id": str(payload.subject_id),
            "state": CaseStatus.OPEN.value,
        },
        request_id=request_id,
    )
    return case


async def list_my_cases(
    db: AsyncSession,
    *,
    user: "User",
    case_types: Sequence[CaseType] | None = None,
    states: Sequence[CaseStatus] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[CaseListItemOut]:
    stmt = select(Case).where(Case.applicant_id == user.id)
    if case_types:
        stmt = stmt.where(Case.case_type.in_([str(t) for t in case_types]))
    if states:
        stmt = stmt.where(Case.state.in_([str(s) for s in states]))
    rows, next_cursor, has_more = await _page(
        db, stmt, model=Case, limit=limit, cursor=cursor
    )
    return CursorPage(
        items=[CaseListItemOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _detail(case: Case, events: Sequence[CaseEvent], *, redact: bool) -> CaseDetailOut:
    """分层返回：本人视角置空内部字段，由路由的 exclude_none 从响应中省略（§13.2、§17.3）。"""
    return CaseDetailOut(
        id=case.id,
        case_type=case.case_type,
        subject_type=case.subject_type,
        subject_id=case.subject_id,
        state=case.state,
        title=case.title,
        description=case.description,
        rule_version=case.rule_version,
        handler_id=None if redact else case.handler_id,
        decided_at=case.decided_at,
        decision_reason=case.decision_reason,
        closed_at=case.closed_at,
        created_at=case.created_at,
        updated_at=case.updated_at,
        events=[
            CaseEventOut(
                id=e.id,
                event_type=e.event_type,
                served_at=e.served_at,
                deadline_at=e.deadline_at,
                note=None if redact else e.note,
                evidence_ref=None if redact else e.evidence_ref,
                actor_id=None if redact else e.actor_id,
                created_at=e.created_at,
            )
            for e in events
        ],
    )


async def get_my_case(
    db: AsyncSession, *, user: "User", case_id: uuid.UUID
) -> CaseDetailOut:
    """本人或处理人可见；本人视角不返回内部备注、证据原文与处理人身份。"""
    case = await db.get(Case, case_id)
    if case is None:
        raise NotFound("案件不存在")
    # 事件流转可能触发 UPDATE 使 server_default/onupdate 列过期；显式刷新，
    # 避免在异步上下文中因隐式懒加载抛 MissingGreenlet。
    await db.refresh(case)

    is_applicant = case.applicant_id == user.id
    if not is_applicant:
        granted = await effective_permissions(db, user)
        if not ({CASE_HANDLE, RIGHTS_NOTICE_HANDLE} & granted):
            # 非本人且非处理人：按统一策略返回 404，不泄露存在性（§25.1）。
            raise NotFound("案件不存在")

    events = list(
        (
            await db.execute(
                select(CaseEvent)
                .where(CaseEvent.case_id == case.id)
                .order_by(CaseEvent.created_at.asc(), CaseEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return _detail(case, events, redact=is_applicant)


# --------------------------------------------------------------------------- #
# 用户侧：侵权通知与反通知（§13.3）
# --------------------------------------------------------------------------- #


async def create_rights_notice(
    db: AsyncSession,
    *,
    claimant: "User",
    payload,
    request_id: str,
) -> RightsNoticeReceiptOut:
    """侵权通知专用入口：无需购买即可提交，但仍限频（§16.2）。"""
    if claimant.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能提交侵权通知")
    if not payload.truthfulness_declared:
        raise InvalidInput("须作出真实性声明", code="TRUTHFULNESS_DECLARATION_REQUIRED")

    allowed = await check_rate_limit(
        f"rights-notice:{claimant.id}",
        limit=settings.RIGHTS_NOTICE_RATE_LIMIT,
        window_seconds=settings.RIGHTS_NOTICE_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise RateLimited("侵权通知提交过于频繁，请稍后再试")

    title = f"侵权通知：{payload.requested_action}"[:300]
    case = Case(
        case_type=CaseType.RIGHTS_NOTICE.value,
        subject_type=str(payload.target_type),
        subject_id=payload.target_id,
        applicant_id=claimant.id,
        state=CaseStatus.OPEN.value,
        title=title,
        # 具体内容地址/版本属可对外转送的说明，写入案件描述供受理与转送使用。
        description=payload.content_ref,
    )
    db.add(case)
    await db.flush()

    notice = RightsNotice(
        case_id=case.id,
        claimant_name=payload.claimant_name,
        claimant_contact=payload.claimant_contact,
        rights_basis_ref=payload.rights_basis_ref,
        target_type=str(payload.target_type),
        target_id=payload.target_id,
        requested_action=payload.requested_action,
        statement_version=payload.statement_version,
        status=RightsNoticeStatus.RECEIVED.value,
        received_at=_now(),
    )
    db.add(notice)
    await db.flush()

    # 证据原文不入库，只保存引用；身份与联系信息仅向受理人员提供（§17.3）。
    db.add(
        CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.SUBMITTED.value,
            evidence_ref=payload.evidence_ref,
            actor_id=claimant.id,
        )
    )

    await emit_event(
        db,
        event_type=DomainEventType.RIGHTS_NOTICE_RECEIVED,
        subject_type="rights_notice",
        subject_id=notice.id,
        payload={
            "case_id": str(case.id),
            "target_type": str(payload.target_type),
            "target_id": str(payload.target_id),
            "claimant_id": str(claimant.id),
        },
        dedupe_key=f"rights-notice-received:{notice.id}",
    )
    await record_audit(
        db,
        action="rights_notice.received",
        object_type="rights_notice",
        object_id=notice.id,
        actor_id=claimant.id,
        after={
            "case_id": str(case.id),
            "target_type": str(payload.target_type),
            "target_id": str(payload.target_id),
            "status": RightsNoticeStatus.RECEIVED.value,
        },
        request_id=request_id,
    )
    await db.flush()
    # 响应只回显工单号与状态，不回显证据或身份信息。
    return RightsNoticeReceiptOut(
        notice_id=notice.id, case_id=case.id, status=RightsNoticeStatus.RECEIVED
    )


async def _is_notified_party(
    db: AsyncSession, *, user: "User", notice: RightsNotice
) -> bool:
    """被通知方判定：通知指向对象的实际权利人（§13.3）。"""
    kind = str(notice.target_type)
    if kind == CaseSubjectType.RESOURCE.value:
        return await _resource_owner_id(db, notice.target_id) == user.id
    if kind == CaseSubjectType.RESOURCE_VERSION.value:
        from app.modules.content.models import ResourceVersion

        version = await db.get(ResourceVersion, notice.target_id)
        if version is None:
            return False
        return await _resource_owner_id(db, version.resource_id) == user.id
    if kind == CaseSubjectType.COMMENT.value:
        from app.modules.interaction.models import Comment

        comment = await db.get(Comment, notice.target_id)
        return comment is not None and comment.user_id == user.id
    if kind in (CaseSubjectType.ORDER.value, CaseSubjectType.ORDER_ITEM.value):
        from app.modules.commerce.models import Order, OrderItem

        order_id = notice.target_id
        if kind == CaseSubjectType.ORDER_ITEM.value:
            item = await db.get(OrderItem, notice.target_id)
            if item is None:
                return False
            order_id = item.order_id
        order = await db.get(Order, order_id)
        return order is not None and order.user_id == user.id
    # OTHER：无法在此确定被通知方，本版不阻断（依赖受理人员核验）。
    return True


async def create_counter_statement(
    db: AsyncSession,
    *,
    user: "User",
    notice_id: uuid.UUID,
    payload,
    request_id: str,
) -> CounterStatement:
    """不存在侵权声明：仅被通知方或合法代理可提交（§16.2）。"""
    notice = await db.get(RightsNotice, notice_id)
    if notice is None:
        raise NotFound("侵权通知不存在")
    if not await _is_notified_party(db, user=user, notice=notice):
        raise Forbidden("只有被通知方或其合法代理可以提交反通知")

    statement = CounterStatement(
        rights_notice_id=notice.id,
        case_id=notice.case_id,
        actor_id=user.id,
        statement_text=payload.statement_text,
        evidence_ref=payload.evidence_ref,
    )
    db.add(statement)
    await db.flush()

    # 记录转送与期限事实。期限按适用法定路径记录：此处沿用电商法第 43 条的十五日口径，
    # 仅作记录字段，不代表所有案件统一适用十五日（§13.3）。
    served_at = _now()
    deadline_at = served_at + timedelta(days=settings.RIGHTS_NOTICE_COUNTER_DEADLINE_DAYS)
    db.add(
        CaseEvent(
            case_id=notice.case_id,
            event_type=CaseEventType.COUNTER_NOTICE.value,
            evidence_ref=payload.evidence_ref,
            served_at=served_at,
            deadline_at=deadline_at,
            actor_id=user.id,
            note="收到反通知；期限为记录字段，按适用法定路径确定，不统一写死十五日。",
        )
    )

    notice.status = RightsNoticeStatus.COUNTER_RECEIVED.value

    await emit_event(
        db,
        event_type=DomainEventType.RIGHTS_NOTICE_COUNTER,
        subject_type="rights_notice",
        subject_id=notice.id,
        payload={"case_id": str(notice.case_id), "actor_id": str(user.id)},
        dedupe_key=f"rights-notice-counter:{notice.id}:{statement.id}",
    )
    await record_audit(
        db,
        action="rights_notice.counter",
        object_type="rights_notice",
        object_id=notice.id,
        actor_id=user.id,
        after={
            "case_id": str(notice.case_id),
            "status": RightsNoticeStatus.COUNTER_RECEIVED.value,
            "deadline_at": deadline_at.isoformat(),
        },
        request_id=request_id,
    )
    await db.flush()
    return statement


# --------------------------------------------------------------------------- #
# 后台侧：案件队列与事件流转
# --------------------------------------------------------------------------- #


async def list_admin_cases(
    db: AsyncSession,
    *,
    case_types: Sequence[CaseType] | None = None,
    states: Sequence[CaseStatus] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[CaseAdminOut]:
    stmt = select(Case)
    if case_types:
        stmt = stmt.where(Case.case_type.in_([str(t) for t in case_types]))
    if states:
        stmt = stmt.where(Case.state.in_([str(s) for s in states]))
    rows, next_cursor, has_more = await _page(
        db, stmt, model=Case, limit=limit, cursor=cursor
    )
    return CursorPage(
        items=[CaseAdminOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def _sync_rights_notice_status(
    db: AsyncSession, *, case: Case, event_type, actor: "User", request_id: str | None
) -> None:
    """案件事件联动侵权通知状态（§13.3）。

    只做**工单进度联动**，不自动作出法律结论：通知状态仅反映所处阶段，
    「已决定」不等于认定侵权成立，结论与责任仍由受理人员按程序作出。

    约束：
    - 案件未关联通知（`rights_notices.case_id` 无对应行）时静默跳过；
    - 状态只前进不回退，已是终态不覆盖（幂等）；
    - 联动时间由 `updated_at` 与审计行共同留痕。
    """
    target = _CASE_EVENT_NOTICE_STATUS.get(str(event_type))
    if target is None:
        return
    notice = await db.scalar(
        select(RightsNotice).where(RightsNotice.case_id == case.id).limit(1)
    )
    if notice is None:
        return
    previous = notice.status
    current_rank = _RIGHTS_NOTICE_ORDER.get(previous)
    if current_rank is None or _RIGHTS_NOTICE_ORDER[target.value] <= current_rank:
        return

    notice.status = target.value
    await db.flush()
    await record_audit(
        db,
        action="rights_notice.status_synced",
        object_type="rights_notice",
        object_id=notice.id,
        actor_id=actor.id,
        before={"status": previous},
        after={"status": notice.status, "case_event": str(event_type)},
        reason="案件事件联动通知进度（不代表侵权认定）",
        request_id=request_id,
    )


async def append_case_event(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor: "User",
    payload,
    request_id: str,
) -> CaseEvent:
    """后台追加案件事件并推进状态（§16.3）。reason 必填，NOTE 不改状态。

    若案件关联了侵权通知，同时按 `_CASE_EVENT_NOTICE_STATUS` 联动通知状态；
    联动只表示工单进度，**不等于认定侵权成立**。
    """
    case = await db.get(Case, case_id)
    if case is None:
        raise NotFound("案件不存在")
    if payload.event_type not in ADMIN_EVENT_TYPES:
        raise InvalidInput("不支持的事件类型")
    reason = (payload.reason or "").strip()
    if not reason:
        raise InvalidInput("reason 必填")

    now = _now()
    event = CaseEvent(
        case_id=case.id,
        event_type=str(payload.event_type),
        evidence_ref=payload.evidence_ref,
        served_at=payload.served_at,
        deadline_at=payload.deadline_at,
        actor_id=actor.id,
        # 事件的 note 承载本次理由；payload.note 可补充说明。
        note=payload.note if payload.note is not None else reason,
    )
    db.add(event)

    decided = payload.event_type == CaseEventType.DECIDED
    if payload.event_type == CaseEventType.TRIAGED:
        case.state = CaseStatus.TRIAGED.value
        case.handler_id = actor.id
    elif payload.event_type == CaseEventType.FORWARDED:
        # 向被通知方转送，案件进入调查阶段。
        case.state = CaseStatus.INVESTIGATING.value
        if case.handler_id is None:
            case.handler_id = actor.id
    elif decided:
        case.state = CaseStatus.DECIDED.value
        case.handler_id = actor.id
        case.decided_at = now
        case.decision_reason = reason
    elif payload.event_type == CaseEventType.CLOSED:
        case.state = CaseStatus.CLOSED.value
        case.closed_at = now
        if case.handler_id is None:
            case.handler_id = actor.id
    # NOTE：仅追加记录，不改状态。

    await _sync_rights_notice_status(
        db, case=case, event_type=payload.event_type, actor=actor, request_id=request_id
    )

    await emit_event(
        db,
        event_type=(
            DomainEventType.CASE_DECIDED if decided else DomainEventType.CASE_UPDATED
        ),
        subject_type="case",
        subject_id=case.id,
        payload={"event_type": str(payload.event_type), "state": case.state},
        dedupe_key=f"case-event:{event.id}",
    )
    await record_audit(
        db,
        action="case.event",
        object_type="case",
        object_id=case.id,
        actor_id=actor.id,
        after={"event_type": str(payload.event_type), "state": case.state},
        reason=reason,
        request_id=request_id,
    )
    await db.flush()
    return event


# --------------------------------------------------------------------------- #
# 后台侧：必要措施与实际效果（§13.3、§13.4）
# --------------------------------------------------------------------------- #


async def _target_resource_id(
    db: AsyncSession, *, target_type: CaseSubjectType, target_id: uuid.UUID
) -> uuid.UUID:
    """把资源类措施对象解析到资源 ID（仅 RESOURCE / RESOURCE_VERSION 支持）。"""
    kind = str(target_type)
    if kind == CaseSubjectType.RESOURCE.value:
        return target_id
    if kind == CaseSubjectType.RESOURCE_VERSION.value:
        from app.modules.content.models import ResourceVersion

        version = await db.get(ResourceVersion, target_id)
        if version is None:
            raise NotFound("目标版本不存在")
        return version.resource_id
    raise InvalidInput("该措施仅支持资源或资源版本对象")


async def _apply_measure_effect(
    db: AsyncSession,
    *,
    measure: CaseMeasure,
    actor: "User",
    request_id: str,
) -> bool:
    """执行措施的实际效果；返回是否真正作用（商业化关闭时为 False）。"""
    kind = str(measure.measure)

    if kind == CaseMeasureKind.UNPUBLISH.value:
        from app.modules.content.models import Resource

        resource = await db.get(Resource, await _target_resource_id(
            db, target_type=measure.target_type, target_id=measure.target_id
        ))
        if resource is None:
            raise NotFound("目标资源不存在")
        resource.publication_status = PublicationStatus.WITHDRAWN.value
        return True

    if kind == CaseMeasureKind.BLOCK_DOWNLOAD.value:
        from app.modules.access.models import DownloadGrant

        resource_id = await _target_resource_id(
            db, target_type=measure.target_type, target_id=measure.target_id
        )
        # 只改状态、不删凭证记录（沿用 beta13 语义）。
        await db.execute(
            update(DownloadGrant)
            .where(
                DownloadGrant.resource_id == resource_id,
                DownloadGrant.status.in_(_ACTIVE_GRANT_STATUSES),
            )
            .values(status=GrantStatus.REVOKED.value)
        )
        return True

    if kind == CaseMeasureKind.RESTRICT_PREVIEW.value:
        from app.modules.access.service import revoke_resource_previews

        resource_id = await _target_resource_id(
            db, target_type=measure.target_type, target_id=measure.target_id
        )
        # 复用 access 模块既有的整资源预览撤权实现，不另写一套。
        await revoke_resource_previews(
            db, actor=actor, resource_id=resource_id, reason=measure.reason or "", request_id=request_id
        )
        return True

    # BLOCK_SALES / FREEZE_REVENUE：商业化关闭，只记录措施行，不改动任何资金（§13.4）。
    return False


async def take_measure(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor: "User",
    payload,
    request_id: str,
) -> tuple[CaseMeasure, bool]:
    """采取必要措施；返回 (措施行, 是否实际作用)。"""
    case = await db.get(Case, case_id)
    if case is None:
        raise NotFound("案件不存在")
    reason = (payload.reason or "").strip()
    if not reason:
        raise InvalidInput("reason 必填")

    measure = CaseMeasure(
        case_id=case.id,
        target_type=str(payload.target_type),
        target_id=payload.target_id,
        measure=str(payload.measure),
        status=CaseMeasureStatus.ACTIVE.value,
        amount_fen=payload.amount_fen,
        effective_at=_now(),
        reason=reason,
        actor_id=actor.id,
    )
    db.add(measure)
    await db.flush()

    applied = await _apply_measure_effect(
        db, measure=measure, actor=actor, request_id=request_id
    )

    db.add(
        CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.MEASURE_TAKEN.value,
            actor_id=actor.id,
            note=f"采取措施 {str(payload.measure)}（对象 {str(payload.target_type)}）。",
        )
    )
    await emit_event(
        db,
        event_type=DomainEventType.CASE_UPDATED,
        subject_type="case",
        subject_id=case.id,
        payload={"event_type": CaseEventType.MEASURE_TAKEN.value, "measure": str(payload.measure)},
        dedupe_key=f"case-measure-taken:{measure.id}",
    )
    await record_audit(
        db,
        action="case.measure.taken",
        object_type="case_measure",
        object_id=measure.id,
        actor_id=actor.id,
        after={
            "case_id": str(case.id),
            "measure": str(payload.measure),
            "target_type": str(payload.target_type),
            "target_id": str(payload.target_id),
            "effect_applied": applied,
        },
        reason=reason,
        request_id=request_id,
    )
    await db.flush()
    return measure, applied


async def release_measure(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    measure_id: uuid.UUID,
    actor: "User",
    request_id: str,
) -> CaseMeasure:
    """解除措施。不机械自动重新上架；如需恢复公开由管理员另行操作（§13.4）。"""
    case = await db.get(Case, case_id)
    if case is None:
        raise NotFound("案件不存在")
    measure = await db.scalar(
        select(CaseMeasure).where(
            CaseMeasure.id == measure_id, CaseMeasure.case_id == case.id
        )
    )
    if measure is None:
        raise NotFound("措施不存在")
    if measure.status == CaseMeasureStatus.RELEASED.value:
        raise Conflict("该措施已解除", code="MEASURE_ALREADY_RELEASED")

    measure.status = CaseMeasureStatus.RELEASED.value
    measure.released_at = _now()

    db.add(
        CaseEvent(
            case_id=case.id,
            event_type=CaseEventType.MEASURE_RELEASED.value,
            actor_id=actor.id,
            note=(
                f"解除措施 {str(measure.measure)}；不自动恢复公开，"
                "如需恢复由管理员按发布流程另行操作。"
            ),
        )
    )
    await emit_event(
        db,
        event_type=DomainEventType.CASE_UPDATED,
        subject_type="case",
        subject_id=case.id,
        payload={"event_type": CaseEventType.MEASURE_RELEASED.value, "measure": str(measure.measure)},
        dedupe_key=f"case-measure-released:{measure.id}",
    )
    await record_audit(
        db,
        action="case.measure.released",
        object_type="case_measure",
        object_id=measure.id,
        actor_id=actor.id,
        after={"status": CaseMeasureStatus.RELEASED.value},
        request_id=request_id,
    )
    await db.flush()
    return measure


async def active_measures(
    db: AsyncSession,
    subject_type: CaseSubjectType | str,
    subject_id: uuid.UUID,
) -> list[CaseMeasure]:
    """对外导出：查询某对象当前生效中的措施（§13.4）。供其他模块调用。"""
    stmt = (
        select(CaseMeasure)
        .where(
            CaseMeasure.target_type == str(subject_type),
            CaseMeasure.target_id == subject_id,
            CaseMeasure.status == CaseMeasureStatus.ACTIVE.value,
        )
        .order_by(CaseMeasure.created_at.desc(), CaseMeasure.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


def measure_out(measure: CaseMeasure, *, applied: bool | None = None) -> MeasureOut:
    """构造措施响应；商业化关闭的措施标注未实际作用。"""
    out = MeasureOut.model_validate(measure)
    if applied is None:
        applied = str(measure.measure) not in tuple(str(m) for m in COMMERCE_MEASURES)
    out.effect_applied = applied
    return out


def counter_statement_out(statement: CounterStatement) -> CounterStatementOut:
    return CounterStatementOut.model_validate(statement)
