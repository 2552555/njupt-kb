"""ratelimit 业务规则：subject 解析、分级、固定窗口计数与超限拒绝。

设计依据（规划设计/pachong.md）：
- §三「API 分级限流」：不同接口完全不同的限制；配置里的数值只是第一层默认值，
  全部走 settings 可调，不写死（0 表示不限制）。
- §四「不要只按照 IP 限制」：校园网 500 学生同一公网 IP，subject 优先登录用户，
  其次会话，最后 IP；同一 IP 下两个登录用户各记各的账，不互相挤占。

计数器写入与审计写在调用方传入的独立会话里（见 deps.get_counter_db）：
请求被拒时业务事务会回滚，已发生的限流计数与审计必须仍然生效。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.errors import RateLimited
from app.modules.ratelimit.models import TIERS, RateLimitCounter

# 动作 -> settings 中的分级限额 dict。新增动作时在此登记，避免在 enforce 里散落 if。
_LIMIT_SETTINGS: dict[str, str] = {
    "login": "RATE_LIMIT_LOGIN",
    "mfa": "RATE_LIMIT_MFA",
    "search": "RATE_LIMIT_SEARCH",
    "browse": "RATE_LIMIT_BROWSE",
    "resource": "RATE_LIMIT_RESOURCE",
    "download": "RATE_LIMIT_DOWNLOAD",
    "upload": "RATE_LIMIT_UPLOAD",
}

# 审计动作：沿用既有审计机制，不新增 domain event 类型。
AUDIT_ACTION_BLOCKED = "ratelimit.blocked"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def tier_for(role: str | None) -> str:
    """角色 -> 限额档位。未登录（None 或未知角色）按 anon。"""
    if role in TIERS and role != "anon":
        return role
    return "anon"


def limits_for(action: str) -> dict[str, int]:
    """动作 -> 分级限额 dict（每窗口次数）。0 表示不限制。"""
    attr = _LIMIT_SETTINGS.get(action)
    if attr is None:
        raise KeyError(f"未知限流动作: {action}")
    return dict(getattr(settings, attr))


def resolve_subject(
    user: Any, session: Any, request: Request
) -> tuple[str, str]:
    """解析限流主体：登录用户优先，其次会话，最后 IP（pachong.md §四）。

    使用 ASGI client 地址；代理头由服务器仅对受信代理解析，
    应用不直接采信可由客户端伪造的 X-Forwarded-For。
    仅用 IP 做兜底——同一校园网出口下的不同用户由更优先的 user/session 区分。
    """
    if user is not None:
        return "user", str(user.id)
    if session is not None:
        return "session", str(session.id)
    client_ip = request.client.host if request.client else None
    return "ip", client_ip or "unknown"


def _window_start(now: datetime, window_seconds: int) -> datetime:
    """对齐到 epoch 的固定窗口起点：同窗口内所有请求命中同一行，窗口切换即重置。"""
    epoch = int(now.timestamp())
    aligned = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


def _retry_after(window_start: datetime, now: datetime, window_seconds: int) -> int:
    """窗口剩余秒数（至少 1 秒，避免客户端立刻重试形成抖动）。"""
    remaining = (window_start + timedelta(seconds=window_seconds) - now).total_seconds()
    return max(1, math.ceil(remaining))


async def enforce(
    db: AsyncSession,
    *,
    action: str,
    user: Any,
    session: Any,
    request: Request,
) -> None:
    """固定窗口计数并执行限额；超限抛 RateLimited(429)，响应带 Retry-After。

    - RATE_LIMIT_ENABLED=False 时直接放行（本地/测试可关）；
    - 限额为 0 的档位表示不限制；
    - 顺带清理 window_start < now - 2*window 的过期行（一条带索引的 DELETE）。
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    limits = limits_for(action)
    subject_kind, subject = resolve_subject(user, session, request)
    tier = tier_for(getattr(user, "role", None) if user is not None else None)
    limit = int(limits.get(tier, 0))
    if limit <= 0:
        return

    # 主动防御：仅 enforce 模式且该主体有未过期的临时处置时收紧额度（AI 对抗 §10.5）。
    # 只收紧、不放宽；检测不可用或观察模式一律返回 1.0，不影响正常业务。
    try:
        from app.modules.defense import service as defense

        factor = await defense.factor_for(db, kind=subject_kind, subject=subject)
    except Exception:  # noqa: BLE001 - 防御不可用不得阻断限流主链路
        factor = 1.0
    if factor < 1.0:
        limit = max(1, int(limit * factor))

    window = max(1, settings.RATE_LIMIT_WINDOW_SECONDS)
    now = _now()
    window_start = _window_start(now, window)

    # 用 window_start 索引定位过期窗口；清理成本随过期行数变化。
    # synchronize_session=False：删除条件含 datetime 比较，ORM 无法在会话内求值
    # （SQLite 读回的 window_start 是 naive），也不需要同步会话状态。
    await db.execute(
        delete(RateLimitCounter)
        .where(
            RateLimitCounter.window_start < now - timedelta(seconds=2 * window)
        )
        .execution_options(synchronize_session=False)
    )

    # 原子自增：INSERT ... ON CONFLICT DO UPDATE SET count = count + 1。
    # pg_insert 的 ON CONFLICT 在 SQLite 与 PostgreSQL 上均可编译（项目先例：
    # interaction/service.py 的收藏、关注、求资料响应）。RETURNING 直接拿新计数，
    # 避免再查一次。
    stmt = (
        pg_insert(RateLimitCounter)
        .values(
            action=action,
            subject_kind=subject_kind,
            subject=subject,
            window_start=window_start,
            count=1,
        )
        .on_conflict_do_update(
            index_elements=["action", "subject_kind", "subject", "window_start"],
            set_={"count": RateLimitCounter.count + 1},
        )
        .returning(RateLimitCounter.count)
    )
    count = (await db.execute(stmt)).scalar_one()

    if count <= limit:
        await db.commit()
        return

    # 超限：先落库计数与审计（本会话独立于业务事务，提交后拒绝请求），再抛 429。
    retry_after = _retry_after(window_start, now, window)
    actor_id = getattr(user, "id", None) if user is not None else None
    if not isinstance(actor_id, uuid.UUID):
        actor_id = None
    await record_audit(
        db,
        action=AUDIT_ACTION_BLOCKED,
        object_type="rate_limit_counter",
        actor_id=actor_id,
        # 只记动作、主体种类与档位；不复制 subject、IP、密码或会话令牌。
        after={
            "action": action,
            "subject_kind": subject_kind,
            "tier": tier,
            "limit": limit,
            "count": count,
            "window_seconds": window,
        },
        reason="超过分级限流",
        request_id=getattr(request.state, "request_id", None),
    )
    await db.commit()
    raise RateLimited(
        "请求过于频繁，请稍后再试",
        details={
            "action": action,
            "tier": tier,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )
