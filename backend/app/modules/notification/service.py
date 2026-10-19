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

from sqlalchemy import Uuid, and_, bindparam, func, or_, select, text, update
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


async def _load_correction(
    session: AsyncSession, correction_id: uuid.UUID
) -> dict[str, uuid.UUID | None] | None:
    """只读 interaction 的 corrections 表（不导入对方模型类，§2.2）。"""
    row = (
        await session.execute(
            text(
                "SELECT submitter_id, handled_by, resource_id, version_id "
                "FROM corrections WHERE id = :correction_id"
            )
            .bindparams(bindparam("correction_id", type_=Uuid()))
            .columns(
                submitter_id=Uuid(), handled_by=Uuid(), resource_id=Uuid(), version_id=Uuid()
            ),
            {"correction_id": correction_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return {
        "submitter_id": row.submitter_id,
        "handled_by": row.handled_by,
        "resource_id": row.resource_id,
        "version_id": row.version_id,
    }


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
    creators = await session.execute(text("SELECT user_id FROM follows WHERE target_type = 'creator' AND target_id = :creator")
                                     .bindparams(bindparam("creator", type_=Uuid())).columns(user_id=Uuid()), {"creator": resource.owner_id})
    recipients = set(await _course_followers(session, resource.course_id)) | {row[0] for row in creators}
    for user_id in recipients:
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


async def _notify_correction_resolved(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """纠错工单处理结果：通知提交人；处理人即提交人时不通知自己（§20.2）。

    收件人只认纠错工单的提交人，处理人优先使用事件发生时的载荷，
    与资料所有权判断解耦；CORRECTION_CREATED 是提交人本人动作，不登记通知。
    """
    correction = await _load_correction(session, event.subject_id)
    if correction is None:
        return []
    status = str((event.payload or {}).get("status", ""))
    if status == "open":
        return []
    submitter_id = correction["submitter_id"]
    handler_id = _payload_uuid(event, "handled_by") or correction["handled_by"]
    if submitter_id == handler_id:
        # 自己处理自己提交的工单，不给自己发通知。
        return []
    outcomes = {"accepted": "已采纳", "fixed": "已修复", "rejected": "未采纳"}
    return [
        _build(
            submitter_id,
            NotificationType.CORRECTION_RESULT,
            "你的纠错已有处理结果",
            f"你提交的纠错{outcomes.get(status, '已处理')}。",
            {
                "correction_id": str(event.subject_id),
                "status": status,
                "resource_id": str(correction["resource_id"]),
                "version_id": str(correction["version_id"]),
            },
        )
    ]


async def _course_followers(session: AsyncSession, course_id: uuid.UUID) -> list[uuid.UUID]:
    """只读 interaction 的 follows 表（不导入对方模型类，§2.2）。"""
    rows = await session.execute(
        text(
            "SELECT user_id FROM follows "
            "WHERE target_type = 'course' AND target_id = :course_id"
        ).bindparams(bindparam("course_id", type_=Uuid())).columns(user_id=Uuid()),
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


async def _notify_request_updated(session: AsyncSession, event: DomainEvent) -> list[Notification]:
    from app.modules.interaction.request_management import get_request
    from app.modules.interaction.service import request_followers
    try:
        row = await get_request(session, event.subject_id)
    except NotFound:
        return []
    payload = event.payload or {}
    response = payload.get("kind") == "response"
    recipients = {row.requester_id} if response else set(await request_followers(session, row.id)) | {row.requester_id}
    status = str(payload.get("status", "open"))
    labels = {"open": "重新寻找资料", "fulfilled": "已确认解决", "closed": "已关闭"}
    body = "你的资料需求收到新的候选资料，请核对后确认。" if response else f"你关注的资料需求{labels.get(status, '状态已更新')}。"
    return [_build(uid, NotificationType.REQUEST_UPDATED, "资料需求有更新", body,
                   {"request_id": str(row.id), "course_id": str(row.course_id)}) for uid in recipients]


# --------------------------------------------------------------------- #
# beta15 用户权责 §4.4：补证、入驻、管理范围、案件与侵权通知的站内通知
# --------------------------------------------------------------------- #


async def _notify_review_needs_info(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """补证：通知版本作者需要补充材料；不发布、不切换公开指针（§14.1、§16.3）。"""
    version = await _load_version(session, event.subject_id)
    if version is None:
        return []
    reason = (event.payload or {}).get("reason")
    body = f"《{version.title}》需要补充材料。"
    if reason:
        body += f"原因：{reason}"
    return [
        _build(
            version.created_by,
            NotificationType.REVIEW_NEEDS_INFO,
            "资料需要补充材料",
            body,
            {"resource_id": str(version.resource_id), "version_id": str(version.id)},
        )
    ]


async def _load_application(session: AsyncSession, application_id: uuid.UUID) -> Any | None:
    from app.modules.creator.models import ContributorApplication

    return await session.get(ContributorApplication, application_id)


async def _notify_contributor_application_submitted(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """入驻申请受理回执：通知申请人本人（§3.4、§4.4）。"""
    applicant_id = _payload_uuid(event, "user_id")
    if applicant_id is None:
        application = await _load_application(session, event.subject_id)
        applicant_id = application.user_id if application is not None else None
    if applicant_id is None:
        return []
    return [
        _build(
            applicant_id,
            NotificationType.CONTRIBUTOR_APPLICATION_RESULT,
            "入驻申请已提交",
            "你的专业贡献者入驻申请已提交，等待平台审核。",
            {"application_id": str(event.subject_id), "status": "applied"},
        )
    ]


async def _notify_contributor_application_decided(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """入驻审核结果：通知申请人；处理人即申请人时不通知自己（§3.4）。"""
    payload = event.payload or {}
    applicant_id = _payload_uuid(event, "user_id")
    decider_id = _payload_uuid(event, "decided_by")
    if applicant_id is None or decider_id is None:
        application = await _load_application(session, event.subject_id)
        if application is not None:
            applicant_id = applicant_id or application.user_id
            decider_id = decider_id or application.decided_by
    if applicant_id is None:
        return []
    if decider_id is not None and decider_id == applicant_id:
        # 处理人即申请人（如管理员自审），不给自己发通知（沿用 beta14 惯例）。
        return []
    status = str(payload.get("status") or payload.get("decision") or "")
    outcomes = {
        "active": "已通过，你已成为入驻贡献者。",
        "rejected": "未通过。",
        "under_review": "已受理，正在审核中。",
        "suspended": "专业身份已暂停，受托管理范围已撤销。既有合同和收益记录保留。",
        "exited": "专业身份已退出。既有合同和收益记录保留。",
    }
    body = f"你的入驻申请{outcomes.get(status, '有新的处理结果')}"
    reason = payload.get("reason")
    if status == "rejected" and reason:
        body += f"原因：{reason}"
    return [
        _build(
            applicant_id,
            NotificationType.CONTRIBUTOR_APPLICATION_RESULT,
            "入驻申请处理结果",
            body,
            {"application_id": str(event.subject_id), "status": status},
        )
    ]


async def _notify_assignment_changed(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """管理范围/权限变更：通知被授予人（§3.3、§4.4）。"""
    user_id = _payload_uuid(event, "user_id")
    if user_id is None:
        return []
    payload = event.payload or {}
    action = str(payload.get("action", ""))
    granted = action != "revoked"
    permission = payload.get("permission")
    if permission:
        body = f"你的权限「{permission}」{'已授予' if granted else '已撤销'}。"
    else:
        body = f"你的资料管理范围{'已更新' if granted else '已撤销'}。"
    return [
        _build(
            user_id,
            NotificationType.ASSIGNMENT_CHANGED,
            "管理范围已更新",
            body,
            {"assignment_id": str(event.subject_id), "action": action},
        )
    ]


async def _load_case(session: AsyncSession, case_id: uuid.UUID) -> Any | None:
    from app.modules.cases.models import Case

    return await session.get(Case, case_id)


async def _load_rights_notice(session: AsyncSession, notice_id: uuid.UUID) -> Any | None:
    from app.modules.cases.models import RightsNotice

    return await session.get(RightsNotice, notice_id)


async def _notify_case_created(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """案件受理：通知申请人（§4.4）。"""
    applicant_id = _payload_uuid(event, "applicant_id")
    case = None
    if applicant_id is None:
        case = await _load_case(session, event.subject_id)
        applicant_id = case.applicant_id if case is not None else None
    if applicant_id is None:
        return []
    return [
        _build(
            applicant_id,
            NotificationType.CASE_RESULT,
            "你的案件已受理",
            "你提交的案件已受理，可在案件详情查看进度。",
            {"case_id": str(event.subject_id)},
        )
    ]


async def _notify_case_progress(
    session: AsyncSession, event: DomainEvent, *, title: str, body: str
) -> list[Notification]:
    """案件进展/裁决：通知申请人；处理人即申请人时不通知自己（§4.4）。"""
    case = await _load_case(session, event.subject_id)
    if case is None:
        return []
    applicant_id = case.applicant_id
    if case.handler_id is not None and case.handler_id == applicant_id:
        return []
    return [
        _build(
            applicant_id,
            NotificationType.CASE_RESULT,
            title,
            body,
            {"case_id": str(case.id), "state": str(case.state)},
        )
    ]


async def _notify_case_updated(session: AsyncSession, event: DomainEvent) -> list[Notification]:
    return await _notify_case_progress(
        session, event, title="案件有新的进展", body="你提交的案件有新的处理进展，可查看详情。"
    )


async def _notify_case_decided(session: AsyncSession, event: DomainEvent) -> list[Notification]:
    return await _notify_case_progress(
        session, event, title="案件已有处理结论", body="你提交的案件已有处理结论，可查看详情。"
    )


async def _notify_rights_notice_received(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """侵权通知受理回执：通知通知人本人（§13.3、§4.4）。"""
    payload = event.payload or {}
    claimant_id = _payload_uuid(event, "claimant_id")
    if claimant_id is None:
        case_id = _payload_uuid(event, "case_id")
        case = await _load_case(session, case_id) if case_id else None
        claimant_id = case.applicant_id if case is not None else None
    if claimant_id is None:
        return []
    return [
        _build(
            claimant_id,
            NotificationType.RIGHTS_NOTICE_RESULT,
            "侵权通知已收到",
            "你的侵权通知已收到，平台将按流程处理。",
            {
                "case_id": str(payload.get("case_id") or ""),
                "rights_notice_id": str(event.subject_id),
            },
        )
    ]


async def _notify_rights_notice_counter(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """反通知：通知原通知人；提交人本人不重复通知（§13.3、§4.4）。"""
    case_id = _payload_uuid(event, "case_id")
    case = await _load_case(session, case_id) if case_id else None
    if case is None:
        return []
    claimant_id = case.applicant_id
    actor_id = _payload_uuid(event, "actor_id")
    if actor_id is not None and actor_id == claimant_id:
        return []
    return [
        _build(
            claimant_id,
            NotificationType.RIGHTS_NOTICE_RESULT,
            "收到反通知",
            "你的侵权通知已收到被通知方的反通知，平台将继续处理。",
            {"case_id": str(case.id), "rights_notice_id": str(event.subject_id)},
        )
    ]


async def _notify_rights_notice_decided(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """侵权通知处理结论：通知原通知人（§4.4）。"""
    case_id = _payload_uuid(event, "case_id")
    case = await _load_case(session, case_id) if case_id else None
    if case is None:
        notice = await _load_rights_notice(session, event.subject_id)
        case = await _load_case(session, notice.case_id) if notice is not None else None
    if case is None:
        return []
    return [
        _build(
            case.applicant_id,
            NotificationType.RIGHTS_NOTICE_RESULT,
            "侵权通知处理结果",
            "你的侵权通知已有处理结果，可查看详情。",
            {"case_id": str(case.id), "rights_notice_id": str(event.subject_id)},
        )
    ]


async def _notify_licensing_event(
    session: AsyncSession, event: DomainEvent
) -> list[Notification]:
    """仅给签约方和有相应权限的平台人员发状态提醒，不携带金额和条款。"""
    from app.core.permissions import AGREEMENT_MANAGE, PRICE_CONFIRM
    from app.modules.assignment.models import AdminPermissionAssignment
    from app.modules.identity.models import User
    from app.modules.licensing.models import AgreementVersion, LicenseAgreement, PriceProposal
    from app.modules.licensing.service import PLATFORM_PARTY_ID

    agreement_event = event.event_type == DomainEventType.AGREEMENT_ACCEPTED
    permission = AGREEMENT_MANAGE if agreement_event else PRICE_CONFIRM
    actor_id = _payload_uuid(event, "actor_id") or _payload_uuid(event, "proposed_by")
    if agreement_event:
        version = await session.get(AgreementVersion, event.subject_id)
        agreement = await session.get(LicenseAgreement, version.agreement_id) if version else None
        if agreement is None:
            return []
        owner_id = agreement.contributor_party_id
        platform_id = agreement.platform_party_id
        acting_party = _payload_uuid(event, "party_id")
        data = {"agreement_id": str(agreement.id)}
    else:
        from app.modules.commerce.models import Product

        proposal = await session.get(PriceProposal, event.subject_id)
        product = await session.get(Product, proposal.product_id) if proposal else None
        if product is None:
            return []
        owner_id = product.owner_id
        platform_id = PLATFORM_PARTY_ID
        acting_party = (
            _payload_uuid(event, "party_id") or _payload_uuid(event, "rejected_by") or actor_id
        )
        data = {"price_proposal_id": str(proposal.id)}

    recipients = set()
    if owner_id and acting_party != owner_id:
        recipients.add(owner_id)
    # 确认价格是双方完成的共同事实，双方都收到结果（处理人除外）。
    confirmed = event.event_type == DomainEventType.PRICE_CONFIRMED
    if confirmed and owner_id:
        recipients.add(owner_id)
    if acting_party != platform_id or confirmed:
        if platform_id != PLATFORM_PARTY_ID:
            recipients.add(platform_id)
        else:
            now = _now()
            delegated = select(AdminPermissionAssignment.user_id).where(
                AdminPermissionAssignment.permission == permission,
                AdminPermissionAssignment.scope == "global",
                AdminPermissionAssignment.scope_catalog_id.is_(None),
                AdminPermissionAssignment.scope_resource_id.is_(None),
                AdminPermissionAssignment.revoked_at.is_(None),
                or_(AdminPermissionAssignment.valid_until.is_(None),
                    AdminPermissionAssignment.valid_until > now),
            )
            recipients.update((await session.scalars(select(User.id).where(
                User.status == "active", or_(User.role == "admin", User.id.in_(delegated)),
            ))).all())
    recipients.discard(actor_id)
    if not recipients:
        return []
    recipients = set((await session.scalars(select(User.id).where(
        User.id.in_(recipients), User.status == "active",
    ))).all())
    title = "授权协议确认进度" if agreement_event else "价格方案状态更新"
    return [_build(
        recipient, NotificationType.AGREEMENT_UPDATED if agreement_event else NotificationType.PRICE_UPDATED,
        title, "相关方案已有更新，请查看当前版本后处理。", data,
    ) for recipient in sorted(recipients)]


_EVENT_HANDLERS: dict[str, _EventHandler] = {
    DomainEventType.RESOURCE_VERSION_SUBMITTED.value: _notify_version_submitted,
    DomainEventType.RESOURCE_VERSION_APPROVED.value: _notify_review_result,
    DomainEventType.RESOURCE_VERSION_REJECTED.value: _notify_review_result,
    DomainEventType.RESOURCE_PUBLISHED.value: _notify_published,
    DomainEventType.RESOURCE_WITHDRAWN.value: _notify_withdrawn,
    DomainEventType.REPORT_RESOLVED.value: _notify_report_resolved,
    # CORRECTION_CREATED 是提交人本人动作，无需站内通知，故意不登记。
    DomainEventType.CORRECTION_RESOLVED.value: _notify_correction_resolved,
    DomainEventType.REQUEST_UPDATED.value: _notify_request_updated,
    # --- beta15 用户权责 §4.4：补证、入驻、管理范围、案件与侵权通知 ---
    DomainEventType.REVIEW_NEEDS_INFO.value: _notify_review_needs_info,
    DomainEventType.CONTRIBUTOR_APPLICATION_SUBMITTED.value: (
        _notify_contributor_application_submitted
    ),
    DomainEventType.CONTRIBUTOR_APPLICATION_DECIDED.value: (
        _notify_contributor_application_decided
    ),
    DomainEventType.ASSIGNMENT_CHANGED.value: _notify_assignment_changed,
    DomainEventType.CASE_CREATED.value: _notify_case_created,
    DomainEventType.CASE_UPDATED.value: _notify_case_updated,
    DomainEventType.CASE_DECIDED.value: _notify_case_decided,
    DomainEventType.RIGHTS_NOTICE_RECEIVED.value: _notify_rights_notice_received,
    DomainEventType.RIGHTS_NOTICE_COUNTER.value: _notify_rights_notice_counter,
    DomainEventType.RIGHTS_NOTICE_DECIDED.value: _notify_rights_notice_decided,
    # --- beta16 授权协议与双向定价：登记分发（暂不构造通知，见处理器 docstring）---
    DomainEventType.AGREEMENT_ACCEPTED.value: _notify_licensing_event,
    DomainEventType.PRICE_PROPOSED.value: _notify_licensing_event,
    DomainEventType.PRICE_CONFIRMED.value: _notify_licensing_event,
    DomainEventType.PRICE_REJECTED.value: _notify_licensing_event,
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
