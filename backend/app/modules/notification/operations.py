"""失败事件队列；重试只重新排队，不在请求内发送通知。"""

from sqlalchemy import select
from pydantic import BaseModel, Field

from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound
from app.core.pagination import CursorPage, encode_cursor, normalize_page_size
from app.modules.notification.models import DomainEvent
from app.modules.notification.service import _decode_cursor


class EventRetry(BaseModel):
    expected_attempts: int = Field(ge=0)


def event_item(row):
    return {
        "id": str(row.id),
        "event_type": row.event_type,
        "status": row.status,
        "attempts": row.attempts,
        "created_at": row.created_at.isoformat(),
    }


async def list_events(db, *, cursor, limit):
    stmt = select(DomainEvent).where(DomainEvent.status == "failed")
    decoded = _decode_cursor(cursor)
    if decoded:
        t, i = decoded
        stmt = stmt.where(
            (DomainEvent.created_at < t)
            | ((DomainEvent.created_at == t) & (DomainEvent.id < i))
        )
    size = normalize_page_size(limit)
    rows = list(
        (
            await db.scalars(
                stmt.order_by(
                    DomainEvent.created_at.desc(), DomainEvent.id.desc()
                ).limit(size + 1)
            )
        ).all()
    )
    more = len(rows) > size
    rows = rows[:size]
    return CursorPage(
        items=[event_item(row) for row in rows],
        has_more=more,
        next_cursor=encode_cursor(
            {"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)}
        )
        if more
        else None,
    )


async def retry_event(db, *, event_id, expected_attempts, user, trace):
    row = await db.scalar(
        select(DomainEvent)
        .where(DomainEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("事件不存在")
    if row.attempts != expected_attempts:
        raise Conflict("事件已变化，请刷新后重试")
    if row.status == "pending":
        return event_item(row)
    if row.status != "failed":
        raise Conflict("仅失败事件可以重新排队")
    row.status = "pending"
    row.last_error = None
    await record_audit(
        db,
        action="event.retry_requested",
        object_type="domain_event",
        object_id=row.id,
        actor_id=user.id,
        request_id=trace,
        after={"status": "pending", "attempts": row.attempts},
    )
    await db.flush()
    return event_item(row)
