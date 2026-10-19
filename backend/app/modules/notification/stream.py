"""notification 实时流（SSE，beta16 §33；buchong 实时推送待办）。

设计要点：
- **只推未读数，不推通知正文**：正文仍由 `/api/v1/notifications` 等受权接口拉取。
  这样即使连接被共享（浏览器多标签、反向代理复用、同账号多端），流里也只暴露
  计数，不会泄露任何通知标题或内容；正文的权限校验始终发生在既有接口。
- **每次复查都用短生命周期 session**：长连接不能在整个存活期间持有一个
  session/事务，否则并发连接数会直接放大数据库连接池压力（§33）。
- **进程内连接计数**：多 worker 部署时每个进程各自限额，只保护单进程的
  并发连接数，不作为跨进程配额；Redis 不可用时也不影响本模块。
- SSE 是增强而非依赖：断流不影响站内通知的既有拉取语义，客户端重连即可。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import Conflict
from app.modules.notification import service

# 会话工厂签名：async_sessionmaker() 返回可 `async with` 的 AsyncSession。
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# 进程内并发连接计数。事件循环内读改写之间没有 await，单进程下无需加锁；
# 不用 asyncio.Lock 是为了避免模块级锁在多个事件循环（测试/多 app 实例）间复用报错。
_active_connections = 0


def active_connections() -> int:
    """当前进程内活跃的实时连接数（仅用于观测与测试）。"""
    return _active_connections


def acquire_connection() -> Callable[[], None]:
    """占用一个连接名额；超过上限抛 `Conflict`（409），由调用方决定是否降级轮询。"""
    global _active_connections
    if _active_connections >= settings.NOTIFICATION_STREAM_MAX_CONNECTIONS:
        raise Conflict(
            "实时连接已达上限，请稍后重试或使用轮询",
            details={"limit": settings.NOTIFICATION_STREAM_MAX_CONNECTIONS},
        )
    _active_connections += 1
    released = False

    def release_once() -> None:
        nonlocal released
        if not released:
            released = True
            release_connection()

    return release_once


def release_connection() -> None:
    """释放连接名额；重复释放不会把计数减成负数。"""
    global _active_connections
    if _active_connections > 0:
        _active_connections -= 1


async def read_unread(
    db_factory: SessionFactory, user_id: uuid.UUID, session_id: uuid.UUID | None = None,
) -> int | None:
    """用独立短会话查询未读数，复用 `service.unread_count` 的查询口径。"""
    async with db_factory() as session:
        if session_id is not None:
            from app.modules.identity.models import Session, User

            valid = await session.scalar(select(Session.id).join(User, User.id == Session.user_id).where(
                Session.id == session_id, Session.user_id == user_id,
                Session.revoked_at.is_(None), Session.expires_at > datetime.now(timezone.utc),
                User.status == "active",
            ))
            if valid is None:
                return None
        return (await service.unread_count(session, user_id=user_id)).unread


def _unread_event(unread: int) -> str:
    """未读数事件：只含整数计数，不含任何通知正文。"""
    data = json.dumps({"unread": unread}, ensure_ascii=False, separators=(",", ":"))
    return f"event: unread\ndata: {data}\n\n"


def _ping_event() -> str:
    """心跳事件：带服务器时间，便于客户端判断连接是否仍然存活。"""
    now = datetime.now(timezone.utc).isoformat()
    data = json.dumps({"t": now}, ensure_ascii=False, separators=(",", ":"))
    return f"event: ping\ndata: {data}\n\n"


async def notification_stream(
    db_factory: SessionFactory,
    *,
    user_id: uuid.UUID,
    heartbeat: float,
    max_seconds: float,
    initial_unread: int | None = None,
    session_id: uuid.UUID | None = None,
    release: Callable[[], None] = release_connection,
) -> AsyncIterator[str]:
    """SSE 事件生成器：首帧未读数、按心跳复查、到期正常结束。

    - 首帧立即推 `unread`，让客户端不必等待第一次心跳。
    - 每个心跳周期推 `ping`，随后复查未读数，**只有变化时**才追加 `unread`，
      避免前端被无意义的重复事件触发刷新。
    - 总时长受 `max_seconds` 约束，到点直接 `return`（不发 error 事件），
      由客户端按需重连。
    - 每次复查都新开短会话，绝不在整个连接期间持有 session/事务。
    - `finally` 无条件释放连接名额：正常结束、异常退出、客户端断开
      （`CancelledError`/`GeneratorExit`）都会归还，避免名额泄漏。
    """
    try:
        unread = (
            initial_unread
            if initial_unread is not None
            else await read_unread(db_factory, user_id, session_id)
        )
        if unread is None:
            return
        yield _unread_event(unread)

        deadline = asyncio.get_running_loop().time() + max_seconds
        # 心跳为 0 或负数时退化为最小间隔，避免忙等占满事件循环。
        interval = max(float(heartbeat), 0.001)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(interval, remaining))
            if asyncio.get_running_loop().time() >= deadline:
                return

            latest = await read_unread(db_factory, user_id, session_id)
            if latest is None:
                return
            yield _ping_event()
            # 复查本身也耗时，超过总时长后不再补推未读数，保证连接按时结束。
            if asyncio.get_running_loop().time() >= deadline:
                return
            if latest != unread:
                unread = latest
                yield _unread_event(unread)
    except asyncio.CancelledError:
        # 客户端主动断开属于正常现象，静默退出，不按 error 级别记录。
        raise
    finally:
        release()
