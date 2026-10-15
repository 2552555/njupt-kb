"""幂等键（设计文档 §25.1、§29）。

影响额度或关键状态的 POST 支持 Idempotency-Key；记录请求摘要，相同键不同请求返回冲突。
支付与结算由唯一业务约束长期防重，不能只依赖本表（§24.3）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, Uuid, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.errors import Conflict, InvalidInput
from app.db import Base, Timestamped, UUIDPrimaryKey

MAX_KEY_LENGTH = 200


class IdempotencyRecord(Base, UUIDPrimaryKey, Timestamped):
    """幂等结果记录。首期保留至少 24 小时（§25.1）。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    scope: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


def request_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_key(key: str | None) -> str:
    if not key:
        raise InvalidInput("该操作需要 Idempotency-Key 请求头")
    key = key.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise InvalidInput("Idempotency-Key 无效")
    return key


async def lookup(
    db: AsyncSession, *, scope: str, key: str, payload: Any, actor_id: uuid.UUID | None
) -> dict[str, Any] | None:
    """命中且请求摘要一致时返回已存响应；摘要不一致返回冲突（§25.1）。"""
    stmt = select(IdempotencyRecord).where(
        IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    now = datetime.now(timezone.utc)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        await db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id == record.id))
        return None
    if record.request_hash != request_hash(payload):
        raise Conflict(
            "相同的 Idempotency-Key 已用于不同的请求",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    if record.response_body is None:
        return None
    return json.loads(record.response_body)


async def remember(
    db: AsyncSession,
    *,
    scope: str,
    key: str,
    payload: Any,
    actor_id: uuid.UUID | None,
    response_status: int,
    response: Any,
) -> None:
    db.add(
        IdempotencyRecord(
            scope=scope,
            key=key,
            actor_id=actor_id,
            request_hash=request_hash(payload),
            response_status=response_status,
            response_body=json.dumps(response, ensure_ascii=False, default=str),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS),
        )
    )


async def purge_expired(db: AsyncSession) -> int:
    result = await db.execute(
        delete(IdempotencyRecord).where(
            IdempotencyRecord.expires_at <= datetime.now(timezone.utc)
        )
    )
    return result.rowcount or 0
