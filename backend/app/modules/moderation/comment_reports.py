"""评论举报与申诉（§18、§20.2）。

设计取舍：
- 复用 moderation 的 ``Report`` 工单表（``comment_id`` 非空即评论举报），不新开重复模型；
- 隐藏/恢复评论复用 interaction 的评论治理能力（``comment_management.manage_comment``），
  不重复实现一套隐藏逻辑；
- 事件类型复用 ``report.created`` / ``report.resolved``，不新增 event_type（domain_events 有共享 CHECK）；
- 状态流转校验修订号（乐观并发），审计与业务在同一事务，审计不复制评论正文。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import AccountStatus, DomainEventType, ReportStatus
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound, RateLimited
from app.core.events import emit_event
from app.core.pagination import CursorPage
from app.core.ratelimit import check_rate_limit
from app.modules.moderation.models import AppealStatus, CommentAppeal, Report, ReportEvent
from app.modules.moderation.schemas import CommentAppealOut, CommentReportOut
from app.modules.moderation.service import _page

# 评论举报沿用 Report 工单：reason_code 用有限分类，自由文本理由存 description。
COMMENT_REPORT_REASON_CODE = "comment"

# 「未处理」举报状态：同一用户对同一评论至多一条，与局部唯一索引保持一致。
ACTIVE_REPORT_STATUSES = (
    ReportStatus.OPEN,
    ReportStatus.TRIAGED,
    ReportStatus.INVESTIGATING,
)

# 处理动作 → 目标状态，以及允许的来源状态（终态 resolved/dismissed 不再处理）。
_COMMENT_REPORT_TARGETS: dict[str, ReportStatus] = {
    "accept": ReportStatus.TRIAGED,
    "dismiss": ReportStatus.DISMISSED,
    "resolve": ReportStatus.RESOLVED,
}
_COMMENT_REPORT_SOURCES: dict[str, frozenset[ReportStatus]] = {
    "accept": frozenset({ReportStatus.OPEN, ReportStatus.APPEALED}),
    "dismiss": frozenset(
        {
            ReportStatus.OPEN,
            ReportStatus.TRIAGED,
            ReportStatus.INVESTIGATING,
            ReportStatus.APPEALED,
        }
    ),
    "resolve": frozenset(
        {
            ReportStatus.OPEN,
            ReportStatus.TRIAGED,
            ReportStatus.INVESTIGATING,
            ReportStatus.APPEALED,
        }
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_reason(reason: str | None, label: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned or len(cleaned) > 500:
        raise InvalidInput(f"{label}须为 1–500 字")
    return cleaned


async def _load_comment(db: AsyncSession, *, resource_id: uuid.UUID, comment_id: uuid.UUID):
    """容错读取评论：评论已被物理删除时返回 None，不阻断工单列表。"""
    from app.modules.interaction.comment_management import get_comment

    try:
        return await get_comment(db, resource_id=resource_id, comment_id=comment_id)
    except NotFound:
        return None


def _comment_summary(comment) -> dict:
    from app.modules.interaction.service import comment_status

    if comment is None:
        return {
            "comment_body": None,
            "comment_status": None,
            "comment_revision": None,
            "comment_author_id": None,
        }
    visible = comment.withdrawn_at is None and not comment.is_hidden
    return {
        "comment_body": comment.body if visible else None,
        "comment_status": comment_status(comment),
        "comment_revision": comment.revision,
        "comment_author_id": comment.user_id,
    }


async def _report_out(db: AsyncSession, report: Report, comment=None) -> CommentReportOut:
    if comment is None and report.comment_id is not None:
        comment = await _load_comment(
            db, resource_id=report.resource_id, comment_id=report.comment_id
        )
    return CommentReportOut(
        id=report.id,
        resource_id=report.resource_id,
        comment_id=report.comment_id,
        reporter_id=report.reporter_id,
        reason=report.description or "",
        status=ReportStatus(report.status),
        revision=report.revision,
        created_at=report.created_at,
        updated_at=report.updated_at,
        **_comment_summary(comment),
    )


def _appeal_out(appeal: CommentAppeal, comment=None) -> CommentAppealOut:
    from app.modules.interaction.service import comment_status

    return CommentAppealOut(
        id=appeal.id,
        comment_id=appeal.comment_id,
        resource_id=appeal.resource_id,
        appellant_id=appeal.appellant_id,
        reason=appeal.reason,
        status=AppealStatus(appeal.status),
        revision=appeal.revision,
        handled_by=appeal.handled_by,
        handled_at=appeal.handled_at,
        created_at=appeal.created_at,
        updated_at=appeal.updated_at,
        comment_status=comment_status(comment) if comment is not None else None,
        comment_revision=comment.revision if comment is not None else None,
    )


# --------------------------------------------------------------------------- #
# 评论举报
# --------------------------------------------------------------------------- #


async def create_comment_report(
    db: AsyncSession,
    *,
    reporter,
    resource_id: uuid.UUID,
    comment_id: uuid.UUID,
    reason: str,
    request_id: str,
) -> CommentReportOut:
    """普通用户举报资料讨论区的一条评论（含一层回复）。"""
    if reporter.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能举报")
    reason = _clean_reason(reason, "举报理由")

    from app.modules.interaction.comment_management import get_comment
    from app.modules.interaction.service import assert_comment_resource

    await assert_comment_resource(db, resource_id=resource_id, user=reporter)
    comment = await get_comment(db, resource_id=resource_id, comment_id=comment_id)
    if comment.user_id == reporter.id:
        raise Forbidden("不能举报自己发布的评论")

    # 频控：按用户维度限制举报提交速率，避免单一账号刷爆治理队列（buchong.md）。
    allowed = await check_rate_limit(
        f"comment-report:{reporter.id}",
        limit=settings.COMMENT_REPORT_RATE_LIMIT,
        window_seconds=settings.COMMENT_REPORT_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise RateLimited("举报提交过于频繁，请稍后再试")

    existing = await db.scalar(
        select(Report.id).where(
            Report.reporter_id == reporter.id,
            Report.comment_id == comment_id,
            Report.status.in_([s.value for s in ACTIVE_REPORT_STATUSES]),
        )
    )
    if existing is not None:
        raise Conflict("你已举报过该评论，正在处理中", code="REPORT_ALREADY_OPEN")

    # 未处理上限：单一账号同时挂起的举报数设上限，防止滥发挤占治理资源（buchong.md）。
    open_count = await db.scalar(
        select(func.count())
        .select_from(Report)
        .where(
            Report.reporter_id == reporter.id,
            Report.comment_id.is_not(None),
            Report.status.in_([s.value for s in ACTIVE_REPORT_STATUSES]),
        )
    )
    if open_count >= settings.COMMENT_REPORT_OPEN_CAP:
        raise RateLimited(
            "你的举报待处理数量已达上限，请等待处理后再提交",
            code="REPORT_OPEN_CAP_EXCEEDED",
        )

    report = Report(
        resource_id=resource_id,
        version_id=None,
        comment_id=comment_id,
        reporter_id=reporter.id,
        reason_code=COMMENT_REPORT_REASON_CODE,
        description=reason,
        status=ReportStatus.OPEN,
        revision=1,
    )
    db.add(report)
    await db.flush()

    db.add(ReportEvent(
        report_id=report.id, actor_id=reporter.id,
        from_status=None, to_status=ReportStatus.OPEN,
    ))

    await emit_event(
        db,
        event_type=DomainEventType.REPORT_CREATED,
        subject_type="report",
        subject_id=report.id,
        payload={
            "resource_id": str(resource_id),
            "comment_id": str(comment_id),
            "reporter_id": str(reporter.id),
            "reason_code": COMMENT_REPORT_REASON_CODE,
        },
        dedupe_key=f"comment-report-created:{report.id}",
    )
    await record_audit(
        db,
        action="comment_report.created",
        object_type="report",
        object_id=report.id,
        object_version=report.revision,
        actor_id=reporter.id,
        after={
            "resource_id": str(resource_id),
            "comment_id": str(comment_id),
            "status": str(ReportStatus.OPEN),
            "revision": report.revision,
        },
        reason=None,
        request_id=request_id,
    )
    return await _report_out(db, report, comment)


async def list_comment_reports(
    db: AsyncSession,
    *,
    statuses: Sequence[ReportStatus] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[CommentReportOut]:
    stmt = select(Report).where(Report.comment_id.is_not(None))
    if statuses:
        stmt = stmt.where(Report.status.in_([str(s) for s in statuses]))
    rows, next_cursor, has_more = await _page(
        db, stmt, model=Report, ts_attr=Report.created_at, limit=limit, cursor=cursor
    )
    items = [await _report_out(db, row) for row in rows]
    return CursorPage[CommentReportOut](items=items, next_cursor=next_cursor, has_more=has_more)


async def _get_comment_report(db: AsyncSession, report_id: uuid.UUID) -> Report:
    report = await db.scalar(select(Report).where(Report.id == report_id))
    if report is None or report.comment_id is None:
        raise NotFound("评论举报不存在")
    return report


async def decide_comment_report(
    db: AsyncSession,
    *,
    report_id: uuid.UUID,
    actor,
    action: str,
    reason: str,
    expected_revision: int,
    comment_revision: int | None,
    request_id: str,
) -> CommentReportOut:
    """管理员处理评论举报：受理 / 驳回 / 处置。

    处置（resolve）复用 beta8 的评论隐藏能力；隐藏与工单流转在同一事务内完成。
    """
    if action not in _COMMENT_REPORT_TARGETS:
        raise InvalidInput("处理动作无效")
    report = await _get_comment_report(db, report_id)
    current = ReportStatus(report.status)
    if current not in _COMMENT_REPORT_SOURCES[action]:
        raise Conflict(
            "不允许的工单流转",
            code="INVALID_REPORT_TRANSITION",
            details={"from": str(current), "action": action},
        )
    reason = _clean_reason(reason, "处理理由")
    target = _COMMENT_REPORT_TARGETS[action]

    if action == "resolve":
        if comment_revision is None:
            raise InvalidInput("处置举报必须提供评论当前修订号")
        from app.modules.interaction.comment_management import manage_comment

        await manage_comment(
            db,
            user=actor,
            resource_id=report.resource_id,
            comment_id=report.comment_id,
            expected_revision=comment_revision,
            hidden=True,
            reason=reason,
            request_id=request_id,
            enforce_permission=False,
        )

    result = await db.execute(
        update(Report)
        .where(
            Report.id == report_id,
            Report.status == current.value,
            Report.revision == expected_revision,
        )
        .values(status=target.value, revision=expected_revision + 1)
    )
    if result.rowcount == 0:
        await db.refresh(report)
        raise Conflict(
            "工单状态已变化，请刷新后重试",
            code="REPORT_STATE_CHANGED",
            details={"status": str(report.status)},
        )
    await db.refresh(report)

    db.add(ReportEvent(
        report_id=report.id, actor_id=actor.id,
        from_status=current.value, to_status=target.value, note=reason,
    ))

    if target == ReportStatus.RESOLVED:
        await emit_event(
            db,
            event_type=DomainEventType.REPORT_RESOLVED,
            subject_type="report",
            subject_id=report_id,
            payload={
                "resource_id": str(report.resource_id),
                "comment_id": str(report.comment_id),
                "reporter_id": str(report.reporter_id),
            },
            dedupe_key=f"comment-report-resolved:{report_id}",
        )
    await record_audit(
        db,
        action="comment_report.decided",
        object_type="report",
        object_id=report_id,
        object_version=report.revision,
        actor_id=actor.id,
        before={"status": str(current), "revision": expected_revision},
        after={"status": str(target), "revision": report.revision, "action": action},
        reason=reason,
        request_id=request_id,
    )
    return await _report_out(db, report)


# --------------------------------------------------------------------------- #
# 评论申诉
# --------------------------------------------------------------------------- #


async def create_comment_appeal(
    db: AsyncSession,
    *,
    appellant,
    resource_id: uuid.UUID,
    comment_id: uuid.UUID,
    reason: str,
    request_id: str,
) -> CommentAppealOut:
    """被管理员隐藏的评论，其作者提交一次申诉。"""
    if appellant.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能提交申诉")
    reason = _clean_reason(reason, "申诉理由")

    from app.modules.interaction.comment_management import get_comment
    from app.modules.interaction.service import assert_comment_resource

    await assert_comment_resource(db, resource_id=resource_id, user=appellant)
    comment = await get_comment(db, resource_id=resource_id, comment_id=comment_id)
    if comment.user_id != appellant.id:
        raise Forbidden("只能申诉自己发布的评论")
    if comment.withdrawn_at is not None:
        raise Conflict("已撤回的评论不能申诉")
    if not comment.is_hidden:
        raise Conflict("该评论未被隐藏，无需申诉")

    # 频控：按用户维度限制申诉提交速率，避免单一账号刷爆治理队列（buchong.md）。
    allowed = await check_rate_limit(
        f"comment-appeal:{appellant.id}",
        limit=settings.COMMENT_APPEAL_RATE_LIMIT,
        window_seconds=settings.COMMENT_APPEAL_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise RateLimited("申诉提交过于频繁，请稍后再试")

    existing = await db.scalar(
        select(CommentAppeal.id).where(CommentAppeal.comment_id == comment_id)
    )
    if existing is not None:
        raise Conflict("该评论已提交过申诉", code="APPEAL_ALREADY_EXISTS")

    # 未处理上限：单一账号同时挂起的申诉数设上限，防止滥发（buchong.md）。
    open_count = await db.scalar(
        select(func.count())
        .select_from(CommentAppeal)
        .where(
            CommentAppeal.appellant_id == appellant.id,
            CommentAppeal.status == AppealStatus.PENDING.value,
        )
    )
    if open_count >= settings.COMMENT_APPEAL_OPEN_CAP:
        raise RateLimited(
            "你的申诉待处理数量已达上限，请等待处理后再提交",
            code="APPEAL_OPEN_CAP_EXCEEDED",
        )

    appeal = CommentAppeal(
        comment_id=comment_id,
        resource_id=resource_id,
        appellant_id=appellant.id,
        reason=reason,
        status=AppealStatus.PENDING,
        revision=1,
    )
    db.add(appeal)
    await db.flush()
    await record_audit(
        db,
        action="comment_appeal.created",
        object_type="comment_appeal",
        object_id=appeal.id,
        object_version=appeal.revision,
        actor_id=appellant.id,
        after={
            "comment_id": str(comment_id),
            "resource_id": str(resource_id),
            "status": str(AppealStatus.PENDING),
            "revision": appeal.revision,
        },
        reason=None,
        request_id=request_id,
    )
    return _appeal_out(appeal, comment)


async def list_comment_appeals(
    db: AsyncSession,
    *,
    statuses: Sequence[AppealStatus] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[CommentAppealOut]:
    stmt = select(CommentAppeal)
    if statuses:
        stmt = stmt.where(CommentAppeal.status.in_([str(s) for s in statuses]))
    rows, next_cursor, has_more = await _page(
        db, stmt, model=CommentAppeal, ts_attr=CommentAppeal.created_at, limit=limit, cursor=cursor
    )
    items = []
    for appeal in rows:
        comment = await _load_comment(
            db, resource_id=appeal.resource_id, comment_id=appeal.comment_id
        )
        items.append(_appeal_out(appeal, comment))
    return CursorPage[CommentAppealOut](items=items, next_cursor=next_cursor, has_more=has_more)


async def _get_appeal(db: AsyncSession, appeal_id: uuid.UUID) -> CommentAppeal:
    appeal = await db.scalar(select(CommentAppeal).where(CommentAppeal.id == appeal_id))
    if appeal is None:
        raise NotFound("申诉不存在")
    return appeal


async def decide_comment_appeal(
    db: AsyncSession,
    *,
    appeal_id: uuid.UUID,
    actor,
    decision: str,
    reason: str,
    expected_revision: int,
    comment_revision: int | None,
    request_id: str,
) -> CommentAppealOut:
    """管理员裁决申诉：采纳（恢复评论）或驳回。"""
    if decision not in ("accept", "reject"):
        raise InvalidInput("裁决动作无效")
    appeal = await _get_appeal(db, appeal_id)
    if AppealStatus(appeal.status) != AppealStatus.PENDING:
        raise Conflict("申诉已裁决", code="APPEAL_ALREADY_DECIDED")
    reason = _clean_reason(reason, "裁决理由")

    if decision == "accept":
        if comment_revision is None:
            raise InvalidInput("采纳申诉必须提供评论当前修订号")
        from app.modules.interaction.comment_management import manage_comment

        await manage_comment(
            db,
            user=actor,
            resource_id=appeal.resource_id,
            comment_id=appeal.comment_id,
            expected_revision=comment_revision,
            hidden=False,
            reason=reason,
            request_id=request_id,
            enforce_permission=False,
        )
    target = AppealStatus.ACCEPTED if decision == "accept" else AppealStatus.REJECTED

    result = await db.execute(
        update(CommentAppeal)
        .where(
            CommentAppeal.id == appeal_id,
            CommentAppeal.status == AppealStatus.PENDING.value,
            CommentAppeal.revision == expected_revision,
        )
        .values(
            status=target.value,
            revision=expected_revision + 1,
            handled_by=actor.id,
            handled_at=_now(),
        )
    )
    if result.rowcount == 0:
        await db.refresh(appeal)
        raise Conflict(
            "申诉状态已变化，请刷新后重试",
            code="STALE_REVISION",
            details={"status": str(appeal.status)},
        )
    await db.refresh(appeal)

    await record_audit(
        db,
        action="comment_appeal.decided",
        object_type="comment_appeal",
        object_id=appeal_id,
        object_version=appeal.revision,
        actor_id=actor.id,
        before={"status": str(AppealStatus.PENDING), "revision": expected_revision},
        after={"status": str(target), "revision": appeal.revision},
        reason=reason,
        request_id=request_id,
    )
    comment = await _load_comment(
        db, resource_id=appeal.resource_id, comment_id=appeal.comment_id
    )
    return _appeal_out(appeal, comment)
