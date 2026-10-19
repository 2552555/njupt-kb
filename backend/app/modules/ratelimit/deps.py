"""ratelimit 的 FastAPI 依赖：可选会话解析、独立计数会话与依赖工厂。

- get_optional_session：可选读取会话（不强制校验、不 401），供限流按会话分桶；
  校验规则与 core.deps.get_current_session 完全一致，只是把 401 换成返回 None。
- get_counter_db：限流计数与审计的独立事务。请求被拒时业务事务会回滚，
  若计数写在业务事务里，超限计数会被一起回滚、限额永远不累计，因此必须独立提交。
  测试通过 dependency_overrides 复用内存库会话。
- rate_limit(action)：依赖工厂，只解析参数并调用 service.enforce，不含业务规则。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_optional_user
from app.core.security import token_digest
from app.db import async_session_factory, get_db
from app.modules.ratelimit.service import enforce


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def get_optional_session(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Any:
    """有会话 Cookie 且会话有效时返回会话记录，否则返回 None（不抛 401）。"""
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        return None
    from app.modules.identity.models import Session as SessionModel

    stmt = select(SessionModel).where(SessionModel.token_digest == token_digest(raw_token))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None
    if _aware(session.expires_at) <= datetime.now(timezone.utc):
        return None
    return session


async def get_counter_db() -> AsyncIterator[AsyncSession]:
    """限流计数专用会话：独立事务提交，不受业务请求回滚影响。"""
    async with async_session_factory() as session:
        yield session


def rate_limit(action: str):
    """限流依赖工厂：在路由签名上加 `_rl: None = Depends(rate_limit("..."))` 即可。

    user 走 get_optional_user（匿名也限流）；session 走 get_optional_session；
    计数写入 get_counter_db 的独立会话。解析与规则分离：本文件只做解析。
    """

    async def _dependency(
        request: Request,
        user: Any = Depends(get_optional_user),
        session: Any = Depends(get_optional_session),
        counter_db: AsyncSession = Depends(get_counter_db),
    ) -> None:
        await enforce(
            counter_db, action=action, user=user, session=session, request=request
        )

    return _dependency
