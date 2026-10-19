"""本人举报与评论申诉进度；历史工单不授予原资料的新读取权。"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ReportStatus
from app.core.errors import NotFound
from app.core.pagination import CursorPage
from app.modules.moderation.models import (
    AppealStatus,
    CommentAppeal,
    Report,
    ReportEvent,
)
from app.modules.moderation.schemas import (
    PersonalCommentAppealOut,
    PersonalReportEventOut,
    PersonalReportOut,
)
from app.modules.moderation.service import _page


async def _visible_targets(db: AsyncSession, user, rows):
    from app.modules.access.visibility import visible_resource_ids
    from app.modules.content.models import Resource, ResourceVersion

    ids = {row.resource_id for row in rows}
    if not ids:
        return {}
    # 历史工单可查，目标只展示仍可读的公开版本；不泄露草稿或已下架标题。
    result = await db.execute(
        select(
            Resource.id, ResourceVersion.id.label("version_id"), ResourceVersion.title
        )
        .join(ResourceVersion, ResourceVersion.id == Resource.published_version_id)
        .where(
            Resource.id.in_(ids),
            Resource.id.in_(visible_resource_ids(user)),
            Resource.publication_status == "published",
            ResourceVersion.resource_id == Resource.id,
            ResourceVersion.review_status == "approved",
        )
    )
    return {row.id: row for row in result}


def _report_out(row, targets):
    target = targets.get(row.resource_id)
    return PersonalReportOut(
        id=row.id,
        kind="comment" if row.comment_id else "resource",
        available=target is not None,
        resource_id=row.resource_id if target else None,
        # 旧版工单不能成为读取非公开版本的入口。
        version_id=row.version_id
        if target and target.version_id == row.version_id
        else None,
        comment_id=row.comment_id if target else None,
        title=target.title if target else None,
        reason_code=row.reason_code,
        description=row.description,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_reports(
    db: AsyncSession,
    *,
    user,
    kind: Literal["resource", "comment"] | None = None,
    statuses: list[ReportStatus] | None = None,
    limit=None,
    cursor=None,
) -> CursorPage[PersonalReportOut]:
    stmt = select(Report).where(Report.reporter_id == user.id)
    if kind == "resource":
        stmt = stmt.where(Report.comment_id.is_(None))
    elif kind == "comment":
        stmt = stmt.where(Report.comment_id.is_not(None))
    if statuses:
        stmt = stmt.where(Report.status.in_([status.value for status in statuses]))
    rows, next_cursor, has_more = await _page(
        db,
        stmt,
        model=Report,
        ts_attr=Report.created_at,
        limit=limit,
        cursor=cursor,
    )
    targets = await _visible_targets(db, user, rows)
    return CursorPage(
        items=[_report_out(row, targets) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def _owned_report(db, user, report_id):
    row = await db.scalar(
        select(Report).where(Report.id == report_id, Report.reporter_id == user.id)
    )
    if row is None:
        raise NotFound("举报工单不存在")
    return row


async def get_report(
    db: AsyncSession, *, user, report_id: uuid.UUID
) -> PersonalReportOut:
    row = await _owned_report(db, user, report_id)
    return _report_out(row, await _visible_targets(db, user, [row]))


async def list_report_events(
    db: AsyncSession,
    *,
    user,
    report_id: uuid.UUID,
    limit=None,
    cursor=None,
) -> CursorPage[PersonalReportEventOut]:
    await _owned_report(db, user, report_id)
    rows, next_cursor, has_more = await _page(
        db,
        select(ReportEvent).where(ReportEvent.report_id == report_id),
        model=ReportEvent,
        ts_attr=ReportEvent.created_at,
        limit=limit,
        cursor=cursor,
    )
    return CursorPage(
        items=[PersonalReportEventOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_comment_appeals(
    db: AsyncSession,
    *,
    user,
    statuses: list[AppealStatus] | None = None,
    limit=None,
    cursor=None,
) -> CursorPage[PersonalCommentAppealOut]:
    stmt = select(CommentAppeal).where(CommentAppeal.appellant_id == user.id)
    if statuses:
        stmt = stmt.where(
            CommentAppeal.status.in_([status.value for status in statuses])
        )
    rows, next_cursor, has_more = await _page(
        db,
        stmt,
        model=CommentAppeal,
        ts_attr=CommentAppeal.created_at,
        limit=limit,
        cursor=cursor,
    )
    targets = await _visible_targets(db, user, rows)
    items = []
    for row in rows:
        target = targets.get(row.resource_id)
        items.append(
            PersonalCommentAppealOut(
                id=row.id,
                available=target is not None,
                resource_id=row.resource_id if target else None,
                comment_id=row.comment_id if target else None,
                title=target.title if target else None,
                reason=row.reason,
                status=row.status,
                handled_at=row.handled_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)
