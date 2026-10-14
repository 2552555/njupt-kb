"""请求级依赖：数据库会话、当前用户、当前会话、请求 ID。

会话事实由 PostgreSQL 持有，Redis 只做缓存（设计文档 §14.2、§37）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.enums import AccountStatus
from app.core.errors import Forbidden, Unauthenticated
from app.core.logging import actor_id_var
from app.core.security import token_digest
from app.db import get_db

__all__ = [
    "get_db",
    "get_request_id",
    "get_client_ip",
    "get_current_session",
    "get_current_user",
    "get_optional_user",
]


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def get_current_session(
    request: Request, db: AsyncSession = Depends(get_db)
) -> "SessionRecord":  # noqa: F821
    from app.modules.identity.models import Session as SessionModel

    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        raise Unauthenticated("需要登录")
    stmt = select(SessionModel).where(SessionModel.token_digest == token_digest(raw_token))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        raise Unauthenticated("会话已失效，请重新登录")
    if _aware(session.expires_at) <= datetime.now(timezone.utc):
        raise Unauthenticated("会话已过期，请重新登录")
    return session


async def get_current_user(
    session: "SessionRecord" = Depends(get_current_session),  # noqa: F821
    db: AsyncSession = Depends(get_db),
) -> "UserRecord":  # noqa: F821
    from app.modules.identity.models import User

    user = (await db.execute(select(User).where(User.id == session.user_id))).scalar_one_or_none()
    if user is None:
        raise Unauthenticated("账号不存在")
    if user.status in (AccountStatus.BANNED, AccountStatus.DELETED):
        raise Forbidden("账号当前不可用", details={"status": str(user.status)})
    actor_id_var.set(str(user.id))
    return user


async def get_optional_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> "UserRecord | None":  # noqa: F821
    if not request.cookies.get(settings.SESSION_COOKIE_NAME):
        return None
    from app.modules.identity.models import Session as SessionModel, User

    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    stmt = select(SessionModel).where(SessionModel.token_digest == token_digest(raw_token))
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None
    if _aware(session.expires_at) <= datetime.now(timezone.utc):
        return None
    user = (await db.execute(select(User).where(User.id == session.user_id))).scalar_one_or_none()
    if user is None or user.status in (AccountStatus.BANNED, AccountStatus.DELETED):
        return None
    return user


def parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    """无效 UUID 返回输入错误，不让转换异常变成 500（§25.1）。"""
    from app.core.errors import InvalidInput

    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidInput(f"{field} 不是合法的 UUID") from exc
