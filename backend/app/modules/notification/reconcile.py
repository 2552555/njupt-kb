"""域事件对账与失败重放（高并发专项 §10.2、§12、§13）。

只读对账 + 失败重放，不新增表、不改动 ``service._EVENT_HANDLERS`` 语义、不直接建通知：

- ``reconcile_events`` 只统计事件账本各状态计数，**不写库**，供管理员与指标端点对账。
- ``replay_failed_events`` 只把未超重放上限的失败事件重新置为 ``pending``，交回既有
  领取机制。既有消费者以 ``status = pending`` 为条件领取（``service._process_event``），
  因此重复调用不会产生重复通知（幂等）；单条更新失败不阻断批量。

达到 ``settings.EVENT_REPLAY_MAX_ATTEMPTS`` 的失败事件按毒任务处理，不再重放。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.modules.notification.models import DomainEvent, DomainEventStatus

__all__ = ["reconcile_events", "replay_failed_events"]

# 对账口径：域事件状态的全部取值（唯一来源 app.modules.notification.models.DomainEventStatus）。
_STATUSES = (
    DomainEventStatus.PENDING,
    DomainEventStatus.PROCESSING,
    DomainEventStatus.PROCESSED,
    DomainEventStatus.FAILED,
)


async def reconcile_events(db: AsyncSession, limit: int = 500) -> dict:
    """只读统计事件账本：各状态计数、超限失败数、重复 dedupe_key 数。不写库。"""
    counts = {
        status.value: int(
            await db.scalar(
                select(func.count(DomainEvent.id)).where(
                    DomainEvent.status == status.value
                )
            )
            or 0
        )
        for status in _STATUSES
    }

    # 超过重放上限的失败事件：视为毒任务，重放会跳过（§10.2）。
    over_max_attempts = int(
        await db.scalar(
            select(func.count(DomainEvent.id)).where(
                DomainEvent.status == DomainEventStatus.FAILED.value,
                DomainEvent.attempts >= settings.EVENT_REPLAY_MAX_ATTEMPTS,
            )
        )
        or 0
    )

    # 重复 dedupe_key：同一 dedupe_key 出现多于一次（跨 event_type 才会重复，
    # 同 event_type + dedupe_key 受唯一约束）。limit 限制返回的分组数。
    duplicate_rows = (
        (
            await db.execute(
                select(DomainEvent.dedupe_key)
                .where(DomainEvent.dedupe_key.is_not(None))
                .group_by(DomainEvent.dedupe_key)
                .having(func.count(DomainEvent.id) > 1)
                .limit(max(1, limit))
            )
        )
        .scalars()
        .all()
    )

    return {
        "pending": counts[DomainEventStatus.PENDING.value],
        "processing": counts[DomainEventStatus.PROCESSING.value],
        "processed": counts[DomainEventStatus.PROCESSED.value],
        "failed": counts[DomainEventStatus.FAILED.value],
        "over_max_attempts": over_max_attempts,
        "duplicate_dedupe_keys": len(duplicate_rows),
    }


async def replay_failed_events(db: AsyncSession, limit: int = 100) -> int:
    """把未超重放上限的失败事件重新置为 ``pending``，返回重置条数（§10.2）。

    复用既有状态字段与既有重试机制，不新增表：置为 ``pending`` 后由既有 fanout/dispatch
    领取；领取以 ``status = pending`` 为条件，重复调用不会产生重复通知。达到
    ``settings.EVENT_REPLAY_MAX_ATTEMPTS`` 的失败事件视为毒任务，不重放。
    """
    candidates = list(
        (
            await db.execute(
                select(DomainEvent.id)
                .where(
                    DomainEvent.status == DomainEventStatus.FAILED.value,
                    DomainEvent.attempts < settings.EVENT_REPLAY_MAX_ATTEMPTS,
                )
                .order_by(DomainEvent.created_at.asc())
                .limit(max(0, limit))
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return 0

    reset = 0
    for event_id in candidates:
        try:
            # 每条一个保存点：单条失败只回滚该条，不阻断批量。
            async with db.begin_nested():
                result = await db.execute(
                    update(DomainEvent)
                    .where(
                        DomainEvent.id == event_id,
                        DomainEvent.status == DomainEventStatus.FAILED.value,
                    )
                    .values(
                        status=DomainEventStatus.PENDING.value, last_error=None
                    )
                )
                if result.rowcount:
                    reset += 1
        except Exception:  # noqa: BLE001 - 单条失败不阻断批量（与 fanout 一致）
            continue

    if reset:
        await record_audit(
            db,
            action="event.replay",
            object_type="domain_event",
            after={"reset": reset, "limit": limit},
            reason="批量重放失败事件",
        )
        await db.flush()
    return reset
