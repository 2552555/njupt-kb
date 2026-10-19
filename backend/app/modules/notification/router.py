"""notification 路由：站内通知的未读数、列表与已读操作（§21）。

router 只做解析与调用，业务规则全部在 service.py（docs/ARCHITECTURE.md §1）。
前缀由 main.py 统一添加，模块不得自行决定前缀。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_current_user, get_db, get_request_id
from app.core.errors import NotEnabled, Unauthenticated
from app.core.permissions import JOB_RETRY, require
from app.db import async_session_factory
from app.modules.notification import service, stream
from app.modules.notification.operations import EventRetry
from app.core.pagination import CursorPage
from app.modules.notification.schemas import NotificationItem, ReadAllResult, UnreadCount

router = APIRouter(tags=["notification"])


@dataclass(frozen=True)
class StreamPrincipal:
    id: uuid.UUID
    session_id: uuid.UUID


class NotificationStreamingResponse(StreamingResponse):
    """即使响应头发送前断开、生成器未启动，也归还连接名额。"""

    def __init__(self, content, *, release: Callable[[], None], **kwargs):
        super().__init__(content, **kwargs)
        self._release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


async def _stream_current_user(request: Request) -> Any:
    """流式端点专用鉴权：用短生命周期 session 校验，返回前即归还数据库连接。

    不能直接 `Depends(get_current_user)`：它的请求级 session 会一直存活到流式
    响应结束，长连接将占住连接池中的一个会话（§33 实时流约束）。
    """
    from app.core.deps import get_current_session

    async with async_session_factory() as session:
        session_record = await get_current_session(request, db=session)
        # 会话关闭后 User 仍可读：未提交事务、expire_on_commit=False，属性已加载。
        user = await get_current_user(session=session_record, db=session)
        return StreamPrincipal(id=user.id, session_id=session_record.id)


@router.get("/admin/events")
async def failed_events(cursor: str | None = None, limit: int = Query(20, ge=1, le=100),
    user=Depends(require(JOB_RETRY)), db: AsyncSession = Depends(get_db)):
    from app.modules.notification.operations import list_events
    return await list_events(db, cursor=cursor, limit=limit)


@router.post("/admin/events/{event_id}/retry")
async def retry_event(event_id: uuid.UUID, payload: EventRetry, user=Depends(require(JOB_RETRY)),
    db: AsyncSession = Depends(get_db), trace=Depends(get_request_id)):
    from app.modules.notification.operations import retry_event
    return await retry_event(db, event_id=event_id, expected_attempts=payload.expected_attempts, user=user, trace=trace)


@router.get("/notifications", response_model=CursorPage[NotificationItem])
async def list_notifications(
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[NotificationItem]:
    return await service.list_notifications(
        db, user_id=user.id, cursor=cursor, limit=limit
    )


@router.get("/notifications/unread-count", response_model=UnreadCount)
async def unread_count(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UnreadCount:
    return await service.unread_count(db, user_id=user.id)


@router.get("/notifications/stream")
async def notification_stream(user=Depends(_stream_current_user)) -> StreamingResponse:
    """未读数实时流（SSE，beta16 §33）。

    只推「未读数变化」的轻量信号，不推通知正文：正文仍由 `/notifications`
    等受权接口拉取。即使连接被共享（多标签、代理复用），流里也没有任何
    通知内容可泄露；正文权限校验保持在既有接口，不受本端点影响。
    开关关闭时抛 `NotEnabled`（503），并发超限抛 `Conflict`（409）。
    """
    if not settings.NOTIFICATION_STREAM_ENABLED:
        raise NotEnabled("通知实时流未启用")

    # 名额在响应开始前占用，超限时能以 409 正常返回，而不是流开始后才失败。
    release = stream.acquire_connection()
    try:
        # 初始未读数用独立短会话读取，读完立即归还连接，不进入流式阶段。
        session_id = getattr(user, "session_id", None)
        initial_unread = await stream.read_unread(async_session_factory, user.id, session_id)
        if initial_unread is None:
            raise Unauthenticated("会话已失效")
    except BaseException:
        # 初始查询失败时立即归还名额，避免连接计数泄漏。
        release()
        raise
    return NotificationStreamingResponse(
        stream.notification_stream(
            async_session_factory,
            user_id=user.id,
            heartbeat=settings.NOTIFICATION_STREAM_HEARTBEAT_SECONDS,
            max_seconds=settings.NOTIFICATION_STREAM_MAX_SECONDS,
            initial_unread=initial_unread,
            session_id=session_id,
            release=release,
        ),
        release=release,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.patch("/notifications/{notification_id}", response_model=NotificationItem)
async def mark_read(
    notification_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationItem:
    return await service.mark_read(db, user_id=user.id, notification_id=notification_id)


@router.post("/notifications/read-all", response_model=ReadAllResult)
async def mark_all_read(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadAllResult:
    return await service.mark_all_read(db, user_id=user.id)
