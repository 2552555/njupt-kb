"""notification 应用服务（设计文档 §21）。

业务规则的唯一落点；router 只做解析与调用（docs/ARCHITECTURE.md §1）。

要点：
- `notify()` 被其他模块在业务事务内调用，只 `db.add`，不自行提交。
- `dispatch_event()` / `fanout_due_events()` 自行用 `async_session_factory` 开事务，
  通知失败只记录 `last_error` 与 `attempts`，不向外抛，因此不会回滚已成功的业务事务。
- 消费者按事件 ID 去重：以 `status = pending` 为条件领取事件，重复消费不再产生通知。
- 投递前用 `access.service.authorize(READ_METADATA)` 校验收件人权限，失去权限则跳过。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Capability, DomainEventType, NotificationType
from app.core.errors import AppError, InvalidInput, NotFound
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.db import async_session_factory
from app.modules.notification.models import DomainEvent, DomainEventStatus, Notification
from app.modules.notification.schemas import NotificationItem, ReadAllResult, UnreadCount

logger = logging.getLogger("app.notification")

# 事件类型 -> 处理函数（仅 §21 P1 需要转成站内通知的事件在此登记）。
_EventHandler = Callable[[AsyncSession, DomainEvent], Awaitable[list[Notification]]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
# 站内通知读写（§21）
# =====================================================================


async def notify(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    type: NotificationType,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> None:
    """在调用方业务事务内追加一条站内通知；只 `db.add`，不自行提交（§21）。"""
    db.add(
        Notification(
            user_id=user_id,
            type=str(type),
            title=title,
            body=body,
            data=data or {},
        )
    )


def _to_item(notification: Notification) -> NotificationItem:
    return NotificationItem(
        id=notification.id,
        type=NotificationType(notification.type),
        title=notification.title,
        body=notification.body,
        data=notification.data or {},
        is_read=notification.is_read,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


async def list_notifications(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
) -> CursorPage[NotificationItem]:
    """本人通知列表，按时间倒序游标分页。"""
    page_size = normalize_page_size(limit)
    stmt = select(Notification).where(Notification.user_id == user_id)
    decoded = _decode_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        stmt = stmt.where(
            or_(
                Notification.created_at < created_at,
                and_(Notification.created_at == created_at, Notification.id < last_id),
            )
        )
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(
        page_size + 1
    )
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if has_more and rows
        else None
    )
    return CursorPage(
        items=[_to_item(row) for row in rows], next_cursor=next_cursor, has_more=has_more
    )


async def unread_count(db: AsyncSession, *, user_id: uuid.UUID) -> UnreadCount:
    total = int(
        (
            await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user_id, Notification.is_read.is_(False)
                )
            )
        ).scalar_one()
        or 0
    )
    return UnreadCount(unread=total)


async def mark_read(
    db: AsyncSession, *, user_id: uuid.UUID, notification_id: uuid.UUID
) -> NotificationItem:
    """设置本人单条通知已读；重复调用结果一致（幂等）。"""
    notification = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if notification is None:
        # 他人通知与不存在统一 404，不泄露存在性（§25.1）。
        raise NotFound("通知不存在")
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = _now()
    return _to_item(notification)


async def mark_all_read(db: AsyncSession, *, user_id: uuid.UUID) -> ReadAllResult:
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True, read_at=_now())
    )
    return ReadAllResult(updated=result.rowcount or 0)


# =====================================================================
# 域事件消费（§21、§29）
# =====================================================================


async def dispatch_event(db: AsyncSession, event_id: uuid.UUID) -> None:
    """消费单条域事件并转为站内通知（§21）。

    `db` 为跨模块固定签名保留；本函数改用 `async_session_factory` 开独立会话自行提交，
    使通知失败不会回滚调用方已成功的业务事务，且异常不向外抛出。
    """
    async with async_session_factory() as session:
        try:
            await _process_event(session, event_id)
        except Exception as exc:  # noqa: BLE001 - 通知失败不得影响调用方（§21）
            logger.warning(
                "event_dispatch_failed",
                extra={"event_id": str(event_id), "error": str(exc)},
            )


async def fanout_due_events(db: AsyncSession, limit: int = 200) -> int:
    """扫描并消费 `status = pending` 的域事件（§21）。返回成功处理的事件数。

    `db` 为跨模块固定签名保留；每条事件使用独立会话与事务，单条失败不影响其余事件。
    """
    async with async_session_factory() as session:
        event_ids = list(
            (
                await session.execute(
                    select(DomainEvent.id)
                    .where(DomainEvent.status == DomainEventStatus.PENDING.value)
                    .order_by(DomainEvent.created_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    processed = 0
    for event_id in event_ids:
        async with async_session_factory() as session:
            try:
                if await _process_event(session, event_id):
                    processed += 1
            except Exception:  # noqa: BLE001 - 单条失败不阻断批量消费
                logger.exception("event_fanout_failed", extra={"event_id": str(event_id)})
    return processed


async def _process_event(session: AsyncSession, event_id: uuid.UUID) -> bool:
    """领取并处理一条事件；返回是否真正处理（重复消费返回 False）。"""
    # 以 pending 为条件领取：并发消费者中只有一个能把事件改为 processing，
    # 其余领取失败即视为重复消费，不再产生通知（§21 按事件 ID 去重）。
    claimed = (
        await session.execute(
            update(DomainEvent)
            .where(
                DomainEvent.id == event_id,
                DomainEvent.status == DomainEventStatus.PENDING.value,
            )
            .values(
                status=DomainEventStatus.PROCESSING.value,
                attempts=DomainEvent.attempts + 1,
            )
            .returning(DomainEvent.id)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await session.rollback()
        return False

    event = (
        await session.execute(select(DomainEvent).where(DomainEvent.id == event_id))
    ).scalar_one()
    try:
        for notification in await _build_notifications(session, event):
            session.add(notification)
        event.status = DomainEventStatus.PROCESSED.value
        event.processed_at = _now()
        event.last_error = None
        await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - 失败只记录，不向外抛（§21）
        await session.rollback()
        await _record_failure(session, event_id, exc)
        return False


async def _record_failure(
    session: AsyncSession, event_id: uuid.UUID, exc: Exception
) -> None:
    """在独立事务中记录失败原因与尝试次数，绝不向外抛。"""
    try:
        event = (
            await session.execute(select(DomainEvent).where(DomainEvent.id == event_id))
        ).scalar_one_or_none()
        if event is None:
            return
        event.status = DomainEventStatus.FAILED.value
        event.attempts = event.attempts + 1
        event.last_error = f"{type(exc).__name__}: {exc}"[:1000]
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("event_failure_record_failed", extra={"event_id": str(event_id)})


async def _build_notifications(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    handler = _EVENT_HANDLERS.get(event.event_type)
    if handler is None:
        # 无对应站内通知的事件（如纯审计事件、P2/P3 事件）标记为已处理即可。
        return []
    return await handler(session, event)


# =====================================================================
# 事件 -> 通知 的收件人与文案
# =====================================================================


def _payload_uuid(event: DomainEvent, key: str) -> uuid.UUID | None:
    raw = (event.payload or {}).get(key)
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _build(
    user_id: uuid.UUID,
    notification_type: NotificationType,
    title: str,
    body: str,
    data: dict[str, Any],
) -> Notification:
    return Notification(
        user_id=user_id,
        type=notification_type.value,
        title=title,
        body=body,
        data=data,
    )


async def _load_resource(session: AsyncSession, resource_id: uuid.UUID) -> Any | None:
    from app.modules.content.service import get_resource

    try:
        return await get_resource(session, resource_id)
    except NotFound:
        return None


async def _load_version(session: AsyncSession, version_id: uuid.UUID) -> Any | None:
    from app.modules.content.service import get_version

    try:
        return await get_version(session, version_id)
    except NotFound:
        return None


async def _notify_version_submitted(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """提交受理：通知版本作者。"""
    version = await _load_version(session, event.subject_id)
    if version is None:
        return []
    return [
        _build(
            version.created_by,
            NotificationType.SUBMISSION_RECEIVED,
            "资料提交已受理",
            f"《{version.title}》已提交，正在等待审核。",
            {"resource_id": str(version.resource_id), "version_id": str(version.id)},
        )
    ]


async def _notify_review_result(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """审核结果：通知版本作者；驳回时附上原因（§39 A06）。"""
    version = await _load_version(session, event.subject_id)
    if version is None:
        return []
    decision = str((event.payload or {}).get("decision", ""))
    reason = (event.payload or {}).get("reason")
    approved = decision == "approve"
    if approved:
        title = "资料审核已通过"
        body = f"《{version.title}》已通过审核。"
    else:
        title = "资料审核未通过"
        body = (
            f"《{version.title}》未通过审核。原因：{reason}"
            if reason
            else f"《{version.title}》未通过审核。"
        )
    return [
        _build(
            version.created_by,
            NotificationType.REVIEW_RESULT,
            title,
            body,
            {
                "resource_id": str(version.resource_id),
                "version_id": str(version.id),
                "decision": decision,
            },
        )
    ]


async def _notify_published(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """版本发布：通知关注该课程的用户；投递前校验其当前仍有元数据读取权。"""
    resource_id = _payload_uuid(event, "resource_id") or event.subject_id
    resource = await _load_resource(session, resource_id)
    if resource is None:
        return []
    version_id = _payload_uuid(event, "version_id") or resource.published_version_id
    version = await _load_version(session, version_id) if version_id else None
    title_text = version.title if version is not None else "资料"

    notifications: list[Notification] = []
    for user_id in await _course_followers(session, resource.course_id):
        if user_id == resource.owner_id:
            continue
        if version_id is None or not await _can_read(
            session, user_id=user_id, resource_id=resource_id, version_id=version_id
        ):
            # 失去权限的收件人不泄露私有更新内容，仅记录跳过原因（§21）。
            logger.info(
                "notification_skipped",
                extra={
                    "event_id": str(event.id),
                    "user_id": str(user_id),
                    "reason": "no_read_access",
                },
            )
            continue
        notifications.append(
            _build(
                user_id,
                NotificationType.VERSION_PUBLISHED,
                "关注的课程有新资料",
                f"《{title_text}》已发布。",
                {
                    "resource_id": str(resource_id),
                    "version_id": str(version_id),
                    "course_id": str(resource.course_id),
                },
            )
        )
    return notifications


async def _notify_withdrawn(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """资源撤下：通知资源作者（其对对象仍有管理权限，不泄露给已失权用户）。"""
    resource_id = _payload_uuid(event, "resource_id") or event.subject_id
    resource = await _load_resource(session, resource_id)
    if resource is None:
        return []
    from app.modules.content.service import published_version

    version = await published_version(session, resource)
    title_text = version.title if version is not None else "资料"
    reason = (event.payload or {}).get("reason")
    body = f"你发布的《{title_text}》已被撤下。"
    if reason:
        body += f"原因：{reason}"
    return [
        _build(
            resource.owner_id,
            NotificationType.RESOURCE_WITHDRAWN,
            "资料已被撤下",
            body,
            {"resource_id": str(resource_id)},
        )
    ]


async def _notify_report_resolved(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """举报处理结果：通知举报人。文案保持通用，不含被举报内容细节。"""
    reporter_id = _payload_uuid(event, "reporter_id")
    if reporter_id is None:
        return []
    return [
        _build(
            reporter_id,
            NotificationType.REPORT_RESULT,
            "举报处理结果",
            "你提交的举报已处理。",
            {"report_id": str(event.subject_id)},
        )
    ]


async def _course_followers(session: AsyncSession, course_id: uuid.UUID) -> list[uuid.UUID]:
    """只读 interaction 的 follows 表（不导入对方模型类，§2.2）。"""
    rows = await session.execute(
        text(
            "SELECT user_id FROM follows "
            "WHERE target_type = 'course' AND target_id = :course_id"
        ),
        {"course_id": course_id},
    )
    return [row[0] for row in rows]


async def _can_read(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
) -> bool:
    """收件人当前是否仍可读取该对象元数据；不通过返回 False（不抛错）。"""
    from app.modules.access.service import authorize
    from app.modules.identity.service import get_user

    try:
        user = await get_user(session, user_id)
    except NotFound:
        return False
    try:
        await authorize(
            session,
            user=user,
            resource_id=resource_id,
            version_id=version_id,
            capability=Capability.READ_METADATA,
        )
        return True
    except AppError:
        return False


_EVENT_HANDLERS: dict[str, _EventHandler] = {
    DomainEventType.RESOURCE_VERSION_SUBMITTED.value: _notify_version_submitted,
    DomainEventType.RESOURCE_VERSION_APPROVED.value: _notify_review_result,
    DomainEventType.RESOURCE_VERSION_REJECTED.value: _notify_review_result,
    DomainEventType.RESOURCE_PUBLISHED.value: _notify_published,
    DomainEventType.RESOURCE_WITHDRAWN.value: _notify_withdrawn,
    DomainEventType.REPORT_RESOLVED.value: _notify_report_resolved,
}


# =====================================================================
# 游标辅助
# =====================================================================


def _decode_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    payload = decode_cursor(cursor)
    try:
        created_at = datetime.fromisoformat(str(payload["t"]))
        last_id = uuid.UUID(str(payload["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at, last_id
