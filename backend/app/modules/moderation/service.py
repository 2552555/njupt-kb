"""审核、举报与审计的应用服务（设计文档 §13.2、§13.3、§18、§27、§29）。

业务规则的唯一落点。`router.py` 只做解析与调用。

跨模块协作（`docs/ARCHITECTURE.md` §2）：
- 表引用只写字符串外键，不导入对方模型类。
- 跨模块服务调用一律在函数体内局部导入，避免模块顶层循环导入。
- 不直接改写其他模块拥有的关键状态列，只调用其服务函数。

并发与幂等（§29）：
- 审核决定使用「状态 + 快照修订号」条件更新，两名审核员并发只有一个生效。
- 同一版本不产生多个有效审核任务，由局部唯一索引 + 冲突回读保证。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import Integer, String, Uuid, column, func, select, table, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.enums import (
    AccountStatus,
    DomainEventType,
    FreshnessStatus,
    ModerationDecisionType,
    PublicationStatus,
    ReportStatus,
    ReviewStatus,
    Role,
)
from app.core.errors import (
    Conflict,
    DependencyUnavailable,
    Forbidden,
    InvalidInput,
    NotFound,
    RateLimited,
)
from app.core.events import emit_event
from app.core.pagination import CursorPage, decode_cursor, encode_cursor, normalize_page_size
from app.core.permissions import RESOURCE_REVIEW, has_permission
from app.core.ratelimit import check_rate_limit
from app.modules.moderation.models import (
    AuditLog,
    ModerationDecision,
    Report,
    ReportEvent,
    Review,
)
from app.modules.moderation.schemas import (
    AuditLogOut,
    ModerationSummaryOut,
    ReportCreateIn,
    ReportEventIn,
    ReportEventOut,
    ReportOut,
    ReviewOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

__all__ = [
    "open_review",
    "get_review",
    "decide",
    "can_review",
    "list_reviews",
    "set_publication",
    "set_freshness",
    "create_report",
    "list_reports",
    "list_report_events",
    "append_report_event",
    "list_audit_logs",
    "moderation_summary",
    "correct_resource_course",
    "set_account_restriction",
]

# §18 投诉工单状态流转：open → triaged → investigating → resolved / dismissed → appealed。
REPORT_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.OPEN: frozenset(
        {ReportStatus.TRIAGED, ReportStatus.INVESTIGATING, ReportStatus.DISMISSED}
    ),
    ReportStatus.TRIAGED: frozenset(
        {ReportStatus.INVESTIGATING, ReportStatus.RESOLVED, ReportStatus.DISMISSED}
    ),
    ReportStatus.INVESTIGATING: frozenset({ReportStatus.RESOLVED, ReportStatus.DISMISSED}),
    ReportStatus.RESOLVED: frozenset({ReportStatus.APPEALED}),
    ReportStatus.DISMISSED: frozenset({ReportStatus.APPEALED}),
    ReportStatus.APPEALED: frozenset(
        {ReportStatus.TRIAGED, ReportStatus.INVESTIGATING, ReportStatus.RESOLVED, ReportStatus.DISMISSED}
    ),
}

REPORT_RATE_LIMIT = 20
REPORT_RATE_WINDOW_SECONDS = 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


async def _page(
    db: AsyncSession,
    stmt,
    *,
    model,
    ts_attr,
    limit: int | None,
    cursor: str | None,
):
    """按 (时间戳, id) 降序的游标分页，默认 20、上限 100（§25.1）。"""
    size = normalize_page_size(limit)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_ts = datetime.fromisoformat(payload["ts"])
            last_id = uuid.UUID(payload["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        stmt = stmt.where(tuple_(ts_attr, model.id) < tuple_(last_ts, last_id))
    stmt = stmt.order_by(ts_attr.desc(), model.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            {"ts": getattr(last, ts_attr.key).isoformat(), "id": str(last.id)}
        )
    return rows, next_cursor, has_more


async def _active_review(db: AsyncSession, version_id: uuid.UUID) -> Review | None:
    """同一版本当前有效的审核任务（draft/pending）。"""
    stmt = (
        select(Review)
        .where(
            Review.version_id == version_id,
            Review.status.in_([ReviewStatus.DRAFT, ReviewStatus.PENDING]),
        )
        .order_by(Review.submitted_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _snapshot_revision(db: AsyncSession, *, version, resource_id: uuid.UUID) -> int:
    """审核快照标识：优先版本自身修订号，其次资源修订号（§12.1、§13.3）。"""
    value = getattr(version, "revision", None)
    if isinstance(value, int) and value > 0:
        return value
    from app.modules.content.service import get_resource

    resource = await get_resource(db, resource_id)
    value = getattr(resource, "revision", None)
    if isinstance(value, int) and value > 0:
        return value
    return 1


async def _version_creator(db: AsyncSession, version_id: uuid.UUID):
    from app.modules.content.service import get_version

    try:
        version = await get_version(db, version_id)
    except NotFound:
        return None
    return getattr(version, "created_by", None)


def _may_decide(review: Review, viewer: "User | None") -> bool | None:
    if viewer is None:
        return None
    if review.submitter_id == viewer.id:
        # §13.3 作者不能审核自己的提交；紧急管理员例外在 decide 中显式记录理由。
        return viewer.role == Role.ADMIN
    return True


def _review_out(review: Review, viewer: "User | None") -> ReviewOut:
    out = ReviewOut.model_validate(review)
    out.can_decide = _may_decide(review, viewer)
    return out


async def _ensure_version_approved(
    db: AsyncSession, *, resource_id: uuid.UUID, version_id: uuid.UUID
) -> None:
    """公开指针必须指向本资源已通过审核的版本（§12.1）。"""
    stmt = (
        select(Review.id)
        .where(
            Review.version_id == version_id,
            Review.resource_id == resource_id,
            Review.status == ReviewStatus.APPROVED,
        )
        .limit(1)
    )
    if (await db.execute(stmt)).first() is not None:
        return

    from app.modules.content.service import get_version

    version = await get_version(db, version_id)
    if getattr(version, "resource_id", None) != resource_id:
        raise InvalidInput("目标版本不属于该资源")
    if str(getattr(version, "review_status", "")) == str(ReviewStatus.APPROVED):
        return
    raise Conflict("目标版本尚未通过审核，不能发布", code="VERSION_NOT_APPROVED")


async def _publish_version(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_id: uuid.UUID,
    request_id: str | None,
) -> None:
    """唯一的公开版本切换入口是 content.service.mark_published（§13.3）。"""
    from app.modules.content.service import get_resource, mark_published

    resource = await get_resource(db, resource_id)
    revision = getattr(resource, "revision", None)
    if not isinstance(revision, int):
        raise DependencyUnavailable("资源缺少 revision，无法安全发布")
    await mark_published(
        db,
        resource_id=resource_id,
        version_id=version_id,
        expected_revision=revision,
        actor_id=actor_id,
        request_id=request_id,
    )


async def _set_version_review_status(
    db: AsyncSession,
    *,
    version_id: uuid.UUID,
    status: ReviewStatus,
    actor_id: uuid.UUID,
    request_id: str,
) -> None:
    """把审核结论投影到版本审核状态（§13.2）。

    发布事务要求 `resource_versions.review_status == approved`（§12.1），该列属于 content，
    因此只能经 content 的服务函数改写（§2.5）。
    """
    from app.modules.content.service import set_version_review_status

    await set_version_review_status(
        db, version_id=version_id, status=status, actor_id=actor_id, request_id=request_id
    )


# --------------------------------------------------------------------------- #
# 契约函数（docs/INTERFACES.md ## moderation）
# --------------------------------------------------------------------------- #


async def open_review(
    db: AsyncSession, *, version_id: uuid.UUID, submitter_id: uuid.UUID
) -> Review:
    """为指定版本开启审核任务；同一版本重复提交返回同一有效任务（§29）。"""
    existing = await _active_review(db, version_id)
    if existing is not None:
        return existing

    from app.modules.content.service import get_version

    version = await get_version(db, version_id)
    resource_id = getattr(version, "resource_id", None)
    if resource_id is None:
        raise InvalidInput("版本缺少资源归属")
    snapshot = await _snapshot_revision(db, version=version, resource_id=resource_id)

    review = Review(
        resource_id=resource_id,
        version_id=version_id,
        submitter_id=submitter_id,
        status=ReviewStatus.PENDING,
        snapshot_revision=snapshot,
        submitted_at=_now(),
    )
    try:
        async with db.begin_nested():
            db.add(review)
            await db.flush()
    except IntegrityError:
        # 并发重复提交：局部唯一索引挡住第二个有效任务，回读已存在任务（§29）。
        existing = await _active_review(db, version_id)
        if existing is None:
            raise
        return existing

    # content.service.submit_version 已写入 RESOURCE_VERSION_SUBMITTED 事件，此处不重复写入。
    await record_audit(
        db,
        action="review.opened",
        object_type="review",
        object_id=review.id,
        object_version=snapshot,
        actor_id=submitter_id,
        after={"status": str(ReviewStatus.PENDING), "version_id": str(version_id)},
        reason=None,
        request_id=None,
    )
    return review


async def get_review(db: AsyncSession, review_id: uuid.UUID) -> Review:
    review = (
        await db.execute(select(Review).where(Review.id == review_id))
    ).scalar_one_or_none()
    if review is None:
        raise NotFound("审核任务不存在")
    return review


async def can_review(db: AsyncSession, reviewer: "User", version_id: uuid.UUID) -> bool:
    """审核员是否可审核该版本。作者不能审核自己的提交（§13.3）。"""
    if not has_permission(reviewer, RESOURCE_REVIEW):
        return False
    review = await _active_review(db, version_id)
    submitter_id = review.submitter_id if review is not None else await _version_creator(db, version_id)
    if submitter_id is not None and submitter_id == reviewer.id:
        return reviewer.role == Role.ADMIN
    return True


async def decide(
    db: AsyncSession,
    *,
    review_id: uuid.UUID,
    reviewer: "User",
    decision: ModerationDecisionType,
    reason: str,
    snapshot_revision: int,
    request_id: str,
) -> ModerationDecision:
    """对指定快照给出审核决定。

    决定不可原地覆盖；并发时过期快照或已处理任务抛 Conflict（§13.3、§29）。
    审核通过时调用 content.service.mark_published 切换公开版本。
    """
    review = await get_review(db, review_id)

    if not has_permission(reviewer, RESOURCE_REVIEW):
        raise Forbidden("没有审核权限")

    reason = (reason or "").strip()
    emergency_self_review = False
    if review.submitter_id == reviewer.id:
        # §13.3 作者不能审核自己的提交；紧急管理员例外必须明确记录理由并保留审计。
        if reviewer.role != Role.ADMIN:
            raise Forbidden("不能审核自己的提交")
        if not reason:
            raise InvalidInput("紧急管理员自审必须填写明确理由")
        emergency_self_review = True

    if decision not in (ModerationDecisionType.APPROVE, ModerationDecisionType.REJECT):
        raise InvalidInput("审核决定无效")

    new_status = (
        ReviewStatus.APPROVED
        if decision == ModerationDecisionType.APPROVE
        else ReviewStatus.REJECTED
    )

    # 条件更新：仅当仍为待审且快照一致时生效；两名审核员并发只有一个生效（§29 A07）。
    result = await db.execute(
        update(Review)
        .where(
            Review.id == review_id,
            Review.status == ReviewStatus.PENDING,
            Review.snapshot_revision == snapshot_revision,
        )
        .values(
            status=new_status,
            decided_at=_now(),
            assigned_reviewer_id=reviewer.id,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        # 强制回读，避免身份映射中的过期实例影响冲突判定。
        await db.refresh(review)
        if review.status != ReviewStatus.PENDING:
            raise Conflict(
                "该审核任务已被处理",
                code="REVIEW_ALREADY_DECIDED",
                details={"status": str(review.status)},
            )
        raise Conflict(
            "审核快照已过期，请刷新后重试",
            code="STALE_SNAPSHOT",
            details={
                "expected_snapshot_revision": review.snapshot_revision,
                "provided_snapshot_revision": snapshot_revision,
            },
        )

    record = ModerationDecision(
        review_id=review_id,
        version_id=review.version_id,
        reviewer_id=reviewer.id,
        decision=decision,
        reason=reason,
        snapshot_revision=snapshot_revision,
    )
    db.add(record)
    await db.flush()

    # 审核结论投影到版本审核状态，作为发布事务的前置条件（§12.1）。
    await _set_version_review_status(
        db,
        version_id=review.version_id,
        status=new_status,
        actor_id=reviewer.id,
        request_id=request_id,
    )

    if decision == ModerationDecisionType.APPROVE:
        await _publish_version(
            db,
            resource_id=review.resource_id,
            version_id=review.version_id,
            actor_id=reviewer.id,
            request_id=request_id,
        )

    await emit_event(
        db,
        event_type=(
            DomainEventType.RESOURCE_VERSION_APPROVED
            if decision == ModerationDecisionType.APPROVE
            else DomainEventType.RESOURCE_VERSION_REJECTED
        ),
        subject_type="resource_version",
        subject_id=review.version_id,
        subject_version=snapshot_revision,
        payload={
            "resource_id": str(review.resource_id),
            "review_id": str(review_id),
            "reviewer_id": str(reviewer.id),
            "decision": str(decision),
            "reason": reason,
        },
        dedupe_key=f"review-decision:{review_id}",
    )

    await record_audit(
        db,
        action="review.decision",
        object_type="review",
        object_id=review_id,
        object_version=snapshot_revision,
        actor_id=reviewer.id,
        before={"status": str(ReviewStatus.PENDING), "snapshot_revision": snapshot_revision},
        after={
            "status": str(new_status),
            "decision": str(decision),
            "version_id": str(review.version_id),
            "emergency_self_review": emergency_self_review,
        },
        reason=reason,
        request_id=request_id,
    )
    return record


# --------------------------------------------------------------------------- #
# 审核队列
# --------------------------------------------------------------------------- #


async def list_reviews(
    db: AsyncSession,
    *,
    statuses: Sequence[ReviewStatus] | None = None,
    submitted_from: datetime | None = None,
    submitted_to: datetime | None = None,
    resource_id: uuid.UUID | None = None,
    version_id: uuid.UUID | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    viewer: "User | None" = None,
) -> CursorPage[ReviewOut]:
    stmt = select(Review)
    if statuses:
        stmt = stmt.where(Review.status.in_([str(s) for s in statuses]))
    if submitted_from is not None:
        stmt = stmt.where(Review.submitted_at >= submitted_from)
    if submitted_to is not None:
        stmt = stmt.where(Review.submitted_at <= submitted_to)
    if resource_id is not None:
        stmt = stmt.where(Review.resource_id == resource_id)
    if version_id is not None:
        stmt = stmt.where(Review.version_id == version_id)

    rows, next_cursor, has_more = await _page(
        db, stmt, model=Review, ts_attr=Review.submitted_at, limit=limit, cursor=cursor
    )
    return CursorPage[ReviewOut](
        items=[_review_out(row, viewer) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------- #
# 发布与时效
# --------------------------------------------------------------------------- #


async def set_publication(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    action: str,
    version_id: uuid.UUID | None,
    expected_revision: int,
    actor: "User",
    reason: str,
    request_id: str,
) -> uuid.UUID:
    """授权发布、恢复或回滚。回滚是一次新的发布动作，记录目标版本、原因、操作者（§12.1）。"""
    from app.modules.content.service import get_resource, mark_published

    resource = await get_resource(db, resource_id)

    if action == "restore":
        target = getattr(resource, "published_version_id", None)
        if target is None:
            raise Conflict("资源没有可恢复的公开版本", code="NO_PUBLISHED_VERSION")
        if resource.publication_status not in (
            PublicationStatus.WITHDRAWN,
            PublicationStatus.ARCHIVED,
        ):
            raise Conflict(
                "资源未处于可恢复状态",
                code="NOT_RESTORABLE",
                details={"publication_status": str(resource.publication_status)},
            )
    else:
        if version_id is None:
            raise InvalidInput("publish / rollback 必须指定目标版本")
        target = version_id

    await _ensure_version_approved(db, resource_id=resource_id, version_id=target)

    before = {
        "publication_status": str(resource.publication_status),
        "published_version_id": (
            str(resource.published_version_id) if resource.published_version_id else None
        ),
    }
    await mark_published(
        db,
        resource_id=resource_id,
        version_id=target,
        expected_revision=expected_revision,
        actor_id=actor.id,
        request_id=request_id,
    )

    # content.service.mark_published 已写入 RESOURCE_PUBLISHED 事件，此处不重复写入。
    await record_audit(
        db,
        action=f"resource.publication.{action}",
        object_type="resource",
        object_id=resource_id,
        object_version=expected_revision,
        actor_id=actor.id,
        before=before,
        after={
            "publication_status": str(PublicationStatus.PUBLISHED),
            "published_version_id": str(target),
        },
        reason=reason,
        request_id=request_id,
    )
    return target


async def set_freshness(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    status: FreshnessStatus,
    actor: "User",
    reason: str | None,
    request_id: str,
) -> None:
    """内容时效是适用性标签，不自动导致删除（§13.2）。"""
    from app.modules.content.service import get_resource
    from app.modules.content.service import set_freshness as content_set_freshness

    resource = await get_resource(db, resource_id)
    before = str(getattr(resource, "freshness_status", ""))
    await content_set_freshness(db, resource_id=resource_id, status=status, actor_id=actor.id)
    await record_audit(
        db,
        action="resource.freshness",
        object_type="resource",
        object_id=resource_id,
        actor_id=actor.id,
        before={"freshness_status": before},
        after={"freshness_status": str(status)},
        reason=reason,
        request_id=request_id,
    )


# --------------------------------------------------------------------------- #
# 举报与投诉工单
# --------------------------------------------------------------------------- #


async def create_report(
    db: AsyncSession,
    *,
    reporter: "User",
    payload: ReportCreateIn,
    request_id: str,
) -> Report:
    """对指定资源或版本举报，进入独立工单（§13.2、§18）。"""
    allowed = await check_rate_limit(
        f"report:{reporter.id}", limit=REPORT_RATE_LIMIT, window_seconds=REPORT_RATE_WINDOW_SECONDS
    )
    if not allowed:
        raise RateLimited("举报过于频繁，请稍后再试")

    from app.modules.content.service import get_resource, get_version

    await get_resource(db, payload.resource_id)

    version_id = payload.version_id
    if version_id is not None:
        version = await get_version(db, version_id)
        if getattr(version, "resource_id", None) != payload.resource_id:
            raise InvalidInput("举报版本不属于该资源")

    report = Report(
        resource_id=payload.resource_id,
        version_id=version_id,
        reporter_id=reporter.id,
        reason_code=payload.reason_code,
        description=payload.description,
        status=ReportStatus.OPEN,
    )
    db.add(report)
    await db.flush()

    db.add(
        ReportEvent(
            report_id=report.id,
            actor_id=reporter.id,
            from_status=None,
            to_status=ReportStatus.OPEN,
            note="举报已受理，等待分流",
            evidence=None,
        )
    )

    await emit_event(
        db,
        event_type=DomainEventType.REPORT_CREATED,
        subject_type="report",
        subject_id=report.id,
        payload={
            "resource_id": str(payload.resource_id),
            "version_id": str(version_id) if version_id else None,
            "reason_code": payload.reason_code,
            "reporter_id": str(reporter.id),
        },
        dedupe_key=f"report-created:{report.id}",
    )
    await record_audit(
        db,
        action="report.created",
        object_type="report",
        object_id=report.id,
        actor_id=reporter.id,
        after={
            "resource_id": str(payload.resource_id),
            "version_id": str(version_id) if version_id else None,
            "reason_code": payload.reason_code,
            "status": str(ReportStatus.OPEN),
        },
        reason=None,
        request_id=request_id,
    )
    return report


async def get_report(db: AsyncSession, report_id: uuid.UUID) -> Report:
    report = (
        await db.execute(select(Report).where(Report.id == report_id))
    ).scalar_one_or_none()
    if report is None:
        raise NotFound("举报工单不存在")
    return report


async def list_reports(
    db: AsyncSession,
    *,
    statuses: Sequence[ReportStatus] | None = None,
    resource_id: uuid.UUID | None = None,
    reporter_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[ReportOut]:
    stmt = select(Report)
    if statuses:
        stmt = stmt.where(Report.status.in_([str(s) for s in statuses]))
    if resource_id is not None:
        stmt = stmt.where(Report.resource_id == resource_id)
    if reporter_id is not None:
        stmt = stmt.where(Report.reporter_id == reporter_id)
    if created_from is not None:
        stmt = stmt.where(Report.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Report.created_at <= created_to)

    rows, next_cursor, has_more = await _page(
        db, stmt, model=Report, ts_attr=Report.created_at, limit=limit, cursor=cursor
    )
    return CursorPage[ReportOut](
        items=[ReportOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def _apply_interim_action(
    db: AsyncSession,
    *,
    report: Report,
    action: str,
    actor: "User",
    reason: str | None,
    request_id: str,
) -> str:
    """高风险内容可先限制访问；临时措施与最终认定分开记录（§18）。"""
    from app.modules.content.service import get_resource, mark_published, mark_withdrawn

    resource = await get_resource(db, report.resource_id)
    revision = getattr(resource, "revision", None)
    if not isinstance(revision, int):
        raise DependencyUnavailable("资源缺少 revision，无法执行临时措施")

    if action == "restrict_access":
        await mark_withdrawn(
            db,
            resource_id=report.resource_id,
            expected_revision=revision,
            actor_id=actor.id,
            reason=reason or "投诉处置中的临时限制访问",
            request_id=request_id,
        )
        return "临时限制访问：仅降低可见性，不代表最终认定违规"
    if action == "release_access":
        published = getattr(resource, "published_version_id", None)
        if published is None:
            raise Conflict("资源没有可恢复的公开版本", code="NO_PUBLISHED_VERSION")
        await mark_published(
            db,
            resource_id=report.resource_id,
            version_id=published,
            expected_revision=revision,
            actor_id=actor.id,
            request_id=request_id,
        )
        return "解除临时限制：恢复原公开版本"
    return "无临时措施"


async def append_report_event(
    db: AsyncSession,
    *,
    report_id: uuid.UUID,
    actor: "User",
    payload: ReportEventIn,
    request_id: str,
) -> ReportEvent:
    """工单流转：open → triaged → investigating → resolved / dismissed → appealed（§18）。"""
    report = await get_report(db, report_id)
    current = ReportStatus(report.status)
    target = payload.to_status
    if target not in REPORT_TRANSITIONS.get(current, frozenset()):
        raise Conflict(
            "不允许的工单状态流转",
            code="INVALID_REPORT_TRANSITION",
            details={"from": str(current), "to": str(target)},
        )

    evidence = dict(payload.evidence or {})
    if payload.interim_action != "none":
        evidence["interim"] = payload.interim_action
        evidence["interim_note"] = await _apply_interim_action(
            db,
            report=report,
            action=payload.interim_action,
            actor=actor,
            reason=payload.note,
            request_id=request_id,
        )

    # 条件更新：状态被其他操作改变时返回冲突（§29）。
    result = await db.execute(
        update(Report)
        .where(Report.id == report_id, Report.status == current)
        .values(status=target)
    )
    if result.rowcount == 0:
        raise Conflict("工单状态已被其他操作改变，请刷新后重试", code="REPORT_STATE_CHANGED")

    event = ReportEvent(
        report_id=report_id,
        actor_id=actor.id,
        from_status=current,
        to_status=target,
        note=payload.note,
        evidence=evidence or None,
    )
    db.add(event)
    await db.flush()

    if target == ReportStatus.RESOLVED:
        await emit_event(
            db,
            event_type=DomainEventType.REPORT_RESOLVED,
            subject_type="report",
            subject_id=report_id,
            payload={
                "resource_id": str(report.resource_id),
                "reporter_id": str(report.reporter_id),
            },
            dedupe_key=f"report-resolved:{report_id}",
        )

    await record_audit(
        db,
        action="report.transition",
        object_type="report",
        object_id=report_id,
        actor_id=actor.id,
        before={"status": str(current)},
        after={"status": str(target), "interim_action": payload.interim_action},
        reason=payload.note,
        request_id=request_id,
    )
    return event


async def list_report_events(
    db: AsyncSession, *, report_id: uuid.UUID, limit: int | None = None, cursor: str | None = None
) -> CursorPage[ReportEventOut]:
    await get_report(db, report_id)
    stmt = select(ReportEvent).where(ReportEvent.report_id == report_id)
    rows, next_cursor, has_more = await _page(
        db, stmt, model=ReportEvent, ts_attr=ReportEvent.created_at, limit=limit, cursor=cursor
    )
    return CursorPage[ReportEventOut](
        items=[ReportEventOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #


async def list_audit_logs(
    db: AsyncSession,
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[AuditLogOut]:
    stmt = select(AuditLog)
    if object_type is not None:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id is not None:
        stmt = stmt.where(AuditLog.object_id == object_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if created_from is not None:
        stmt = stmt.where(AuditLog.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(AuditLog.created_at <= created_to)

    rows, next_cursor, has_more = await _page(
        db, stmt, model=AuditLog, ts_attr=AuditLog.created_at, limit=limit, cursor=cursor
    )
    return CursorPage[AuditLogOut](
        items=[AuditLogOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# --------------------------------------------------------------------------- #
# 后台概览
# --------------------------------------------------------------------------- #


async def moderation_summary(db: AsyncSession) -> ModerationSummaryOut:
    reviews: dict[str, int] = {str(s): 0 for s in ReviewStatus}
    for status_value, count in (
        await db.execute(select(Review.status, func.count()).group_by(Review.status))
    ).all():
        reviews[str(status_value)] = int(count)

    reports: dict[str, int] = {str(s): 0 for s in ReportStatus}
    for status_value, count in (
        await db.execute(select(Report.status, func.count()).group_by(Report.status))
    ).all():
        reports[str(status_value)] = int(count)

    oldest = (
        await db.execute(
            select(func.min(Review.submitted_at)).where(Review.status == ReviewStatus.PENDING)
        )
    ).scalar()

    return ModerationSummaryOut(
        reviews=reviews,
        reports=reports,
        oldest_pending_submitted_at=oldest,
        generated_at=_now(),
    )


# --------------------------------------------------------------------------- #
# 目录维护与账号限制（最小实现，委托给所属模块）
# --------------------------------------------------------------------------- #


async def correct_resource_course(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    course_id: uuid.UUID,
    actor: "User",
    reason: str,
    request_id: str,
) -> None:
    """更正课程归属。`resources.course_id` 属于 content，本模块只发起更正（§11.2、§12.1）。"""
    from app.modules.content.service import reassign_resource_course

    # content 会校验课程存在、做条件更新并留痕；归属更正需要管理员授权（路由层已校验）。
    await reassign_resource_course(
        db,
        resource_id=resource_id,
        course_id=course_id,
        actor_id=actor.id,
        reason=reason,
        request_id=request_id,
    )


async def set_account_restriction(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    suspend: bool,
    reason: str | None,
    until: datetime | None,
    actor: "User",
    request_id: str,
) -> None:
    """暂停/恢复账号。封禁时撤销全部会话（§14.1、§14.2）。"""
    from app.modules.identity import service as identity_service
    from app.modules.identity.service import get_user, revoke_all_sessions

    target = await get_user(db, user_id)
    status = AccountStatus.SUSPENDED if suspend else AccountStatus.ACTIVE
    before_status = str(getattr(target, "status", ""))

    handler = getattr(identity_service, "set_account_status", None)
    if handler is None:
        # 契约缺口：INTERFACES.md 未声明账号状态变更函数。显式失败而非静默成功（§25.2）。
        raise DependencyUnavailable(
            "identity 模块尚未提供账号状态变更服务",
            details={"expected": "identity.service.set_account_status"},
        )

    await handler(
        db,
        user_id=user_id,
        status=status,
        reason=reason,
        actor_id=actor.id,
        request_id=request_id,
    )
    if suspend:
        await revoke_all_sessions(db, user_id, reason=reason or "账号被暂停")

    await record_audit(
        db,
        action="account.suspend" if suspend else "account.reinstate",
        object_type="user",
        object_id=user_id,
        actor_id=actor.id,
        before={"status": before_status},
        after={
            "status": str(status),
            "until": until.isoformat() if until is not None else None,
        },
        reason=reason,
        request_id=request_id,
    )
