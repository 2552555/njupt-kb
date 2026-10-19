"""需求处理与候选回应；所有解决资料按公开元数据权限重新校验。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.enums import AccountStatus, DomainEventType
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.events import emit_event
from app.core.pagination import CursorPage, encode_cursor, normalize_page_size
from app.core.permissions import REQUEST_HANDLE, effective_permissions
from app.modules.interaction.models import ResourceRequest, ResourceRequestResponse
from app.modules.interaction.schemas import RequestItem, RequestResponseItem


async def public_summaries(db, pairs, user=None):
    """批量解析解决资源摘要；可见性按查看者判定（user=None 即匿名）。

    不可访问（不存在/未发布/查看者无可见性/已撤销发布）的条目不返回，
    调用方据此只暴露「原解决资料当前不可访问」，不泄露受限标题。
    """
    from sqlalchemy import Uuid, String, column, table
    from app.modules.access.visibility import visible_resource_ids

    valid = {(rid, cid) for rid, cid in pairs if rid is not None}
    if not valid:
        return {}
    r = table(
        "resources",
        column("id", Uuid),
        column("course_id", Uuid),
        column("published_version_id", Uuid),
        column("publication_status", String),
    )
    v = table(
        "resource_versions",
        column("id", Uuid),
        column("resource_id", Uuid),
        column("title", String),
        column("review_status", String),
    )
    stmt = (
        select(r.c.id, r.c.course_id, v.c.title)
        .select_from(r.join(v, v.c.id == r.c.published_version_id))
        .where(
            r.c.id.in_([rid for rid, _ in valid]),
            r.c.id.in_(visible_resource_ids(user)),
            r.c.publication_status == "published",
            v.c.resource_id == r.c.id,
            v.c.review_status == "approved",
        )
    )
    rows = (await db.execute(stmt)).all()
    return {
        (row.id, row.course_id): (row.id, row.title)
        for row in rows
        if (row.id, row.course_id) in valid
    }


async def public_resource(db, resource_id, course_id):
    result = (await public_summaries(db, [(resource_id, course_id)])).get(
        (resource_id, course_id)
    )
    if result is None:
        from app.modules.content.service import get_resource

        resource = await get_resource(db, resource_id)
        if resource.course_id != course_id:
            raise InvalidInput("解决资料必须属于同一门课程")
        raise NotFound("资料未公开、未通过审核或当前不可访问")
    return result


async def resolution_summary(db, resource_id, course_id, user=None):
    return (await public_summaries(db, [(resource_id, course_id)], user=user)).get(
        (resource_id, course_id), (None, None)
    )


async def get_request(db, request_id, *, lock=False):
    stmt = select(ResourceRequest).where(ResourceRequest.id == request_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None:
        raise NotFound("需求不存在")
    return row


async def request_detail(db, request_id, viewer=None) -> RequestItem:
    from app.modules.interaction.service import _request_item

    return await _request_item(db, await get_request(db, request_id), viewer=viewer)


def active(user):
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("当前账号不能处理或回应需求")


async def change_status(
    db, *, user, request_id, payload, request_trace_id
) -> RequestItem:
    from app.modules.interaction.service import _request_item

    active(user)
    row = await get_request(db, request_id, lock=True)
    manager = REQUEST_HANDLE in await effective_permissions(db, user)
    if not manager and row.requester_id != user.id:
        raise Forbidden("仅发起者或管理员可以处理需求")
    if row.status == "closed" and payload.status == "open" and not manager:
        raise Forbidden("关闭的需求仅管理员可重新打开")
    resource_id = payload.resource_id
    if payload.status == "fulfilled" and resource_id is None:
        raise InvalidInput("请选择解决资料")
    if payload.status != "fulfilled" and resource_id is not None:
        raise InvalidInput("该操作不能附带解决资料")
    if row.status == payload.status and row.resolved_resource_id == resource_id:
        return await _request_item(db, row, viewer=user)
    if row.revision != payload.expected_revision:
        raise Conflict("需求已变化，请刷新后重试", code="STALE_REVISION")
    if (row.status, payload.status) not in {
        ("open", "fulfilled"),
        ("open", "closed"),
        ("fulfilled", "open"),
        ("closed", "open"),
    }:
        raise Conflict("不支持此状态流转，请先重新打开需求")
    reason = (payload.reason or "").strip()
    if payload.status != "fulfilled" and not reason:
        raise InvalidInput("请填写关闭或重新打开的理由")
    if resource_id:
        await public_resource(db, resource_id, row.course_id)
    before = {
        "status": row.status,
        "revision": row.revision,
        "resolved_resource_id": str(row.resolved_resource_id)
        if row.resolved_resource_id
        else None,
    }
    row.status = payload.status
    row.resolved_resource_id = resource_id
    row.handled_by = user.id
    row.handled_at = datetime.now(timezone.utc)
    row.revision += 1
    await record_audit(
        db,
        action="request.status_changed",
        object_type="resource_request",
        object_id=row.id,
        object_version=row.revision,
        actor_id=user.id,
        before=before,
        after={
            "status": row.status,
            "revision": row.revision,
            "resolved_resource_id": str(row.resolved_resource_id)
            if row.resolved_resource_id
            else None,
        },
        reason=reason or None,
        request_id=request_trace_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.REQUEST_UPDATED,
        subject_type="resource_request",
        subject_id=row.id,
        subject_version=row.revision,
        payload={"status": row.status, "course_id": str(row.course_id)},
        dedupe_key=f"request.status:{row.id}:{row.revision}",
    )
    return await _request_item(db, row, viewer=user)


async def reopen(db, *, user, request_id, payload, request_trace_id) -> RequestItem:
    """复核重开：解决资源失效（下架/权限收紧）后由发起者或管理员显式重开。

    保持既有处理权限：已关闭需求仅管理员可重开；理由必填。
    只回到 open 并清空解决资源，不附带新的解决资料。
    """
    from app.modules.interaction.service import _request_item

    active(user)
    row = await get_request(db, request_id, lock=True)
    manager = REQUEST_HANDLE in await effective_permissions(db, user)
    if not manager and row.requester_id != user.id:
        raise Forbidden("仅发起者或管理员可以复核重开需求")
    if row.status == "open":
        raise Conflict("需求已是打开状态")
    if row.status not in ("fulfilled", "closed"):
        raise Conflict("不支持此状态流转")
    if row.status == "closed" and not manager:
        raise Forbidden("关闭的需求仅管理员可重新打开")
    if row.revision != payload.expected_revision:
        raise Conflict("需求已变化，请刷新后重试", code="STALE_REVISION")
    reason = payload.reason.strip()
    before = {
        "status": row.status,
        "revision": row.revision,
        "resolved_resource_id": str(row.resolved_resource_id)
        if row.resolved_resource_id
        else None,
    }
    row.status = "open"
    row.resolved_resource_id = None
    row.handled_by = user.id
    row.handled_at = datetime.now(timezone.utc)
    row.revision += 1
    await record_audit(
        db,
        action="request.reopened",
        object_type="resource_request",
        object_id=row.id,
        object_version=row.revision,
        actor_id=user.id,
        before=before,
        after={
            "status": row.status,
            "revision": row.revision,
            "resolved_resource_id": None,
        },
        reason=reason,
        request_id=request_trace_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.REQUEST_UPDATED,
        subject_type="resource_request",
        subject_id=row.id,
        subject_version=row.revision,
        # payload 只放需求 ID 与修订号：不复制解决资源标题/正文，也不新增事件类型
        # （domain_events.event_type 有共享 CHECK 约束）。
        payload={"request_id": str(row.id), "revision": row.revision},
        dedupe_key=f"request.reopen:{row.id}:{row.revision}",
    )
    return await _request_item(db, row, viewer=user)


async def response_item(db, row, course_id, summary=None):
    resource_id, title = (
        summary
        if summary is not None
        else await resolution_summary(db, row.resource_id, course_id)
    )
    return RequestResponseItem(
        id=row.id,
        request_id=row.request_id,
        submitted_by=row.submitted_by,
        resource_id=resource_id,
        resource_title=title,
        available=resource_id is not None,
        created_at=row.created_at,
    )


async def respond(db, *, user, request_id, resource_id):
    active(user)
    row = await get_request(db, request_id, lock=True)
    if row.status != "open":
        raise Conflict("需求当前不再接受回应")
    await public_resource(db, resource_id, row.course_id)
    created_id = await db.scalar(
        insert(ResourceRequestResponse)
        .values(
            request_id=row.id,
            resource_id=resource_id,
            submitted_by=user.id,
        )
        .on_conflict_do_nothing(index_elements=["request_id", "resource_id"])
        .returning(ResourceRequestResponse.id)
    )
    response = await db.scalar(
        select(ResourceRequestResponse).where(
            ResourceRequestResponse.request_id == row.id,
            ResourceRequestResponse.resource_id == resource_id,
        )
    )
    if created_id is not None:
        await emit_event(
            db,
            event_type=DomainEventType.REQUEST_UPDATED,
            subject_type="resource_request",
            subject_id=row.id,
            payload={"kind": "response", "course_id": str(row.course_id)},
            dedupe_key=f"request.response:{response.id}",
        )
    return await response_item(db, response, row.course_id)


async def list_responses(
    db: AsyncSession, request_id: uuid.UUID, *, cursor=None, limit=None
):
    from app.modules.interaction.service import _decode_time_cursor

    row = await get_request(db, request_id)
    stmt = select(ResourceRequestResponse).where(
        ResourceRequestResponse.request_id == request_id
    )
    decoded = _decode_time_cursor(cursor)
    if decoded:
        t, i = decoded
        stmt = stmt.where(
            (ResourceRequestResponse.created_at < t)
            | (
                (ResourceRequestResponse.created_at == t)
                & (ResourceRequestResponse.id < i)
            )
        )
    size = normalize_page_size(limit)
    rows = list(
        (
            await db.scalars(
                stmt.order_by(
                    ResourceRequestResponse.created_at.desc(),
                    ResourceRequestResponse.id.desc(),
                ).limit(size + 1)
            )
        ).all()
    )
    more = len(rows) > size
    rows = rows[:size]
    summaries = await public_summaries(
        db, [(response.resource_id, row.course_id) for response in rows]
    )
    return CursorPage(
        items=[
            await response_item(
                db,
                response,
                row.course_id,
                summaries.get((response.resource_id, row.course_id), (None, None)),
            )
            for response in rows
        ],
        has_more=more,
        next_cursor=encode_cursor(
            {"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)}
        )
        if more
        else None,
    )
