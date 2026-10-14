"""域事件写入（设计文档 §21）。

业务事务同时写入待处理事件记录，后台将其转换为通知。
消费者按事件 ID 去重；通知失败不能回滚已经成功的资源发布。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DomainEventType


async def emit_event(
    db: AsyncSession,
    *,
    event_type: DomainEventType,
    subject_type: str,
    subject_id: uuid.UUID | str,
    subject_version: int | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> uuid.UUID:
    """在调用方事务内写入一条待处理事件；由 notification 模块拥有 domain_events 表。"""
    from app.modules.notification.models import DomainEvent

    event = DomainEvent(
        event_type=str(event_type),
        subject_type=subject_type,
        subject_id=subject_id,
        subject_version=subject_version,
        payload=payload or {},
        dedupe_key=dedupe_key,
    )
    db.add(event)
    await db.flush()
    return event.id
