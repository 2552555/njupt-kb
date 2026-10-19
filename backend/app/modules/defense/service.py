"""主动防御业务规则：信号采集、风险评分、处置与诱饵登记。

设计依据《AI 对抗攻击防御设计》§10：

- 单信号不定性：只有多类信号在窗口内累积才提升风险等级（§10.4）；
- 观察优先：默认 ``DEFENSE_MODE=observe``，只评估与留痕，不改变正常业务结果（§10.5）；
- 处置有界：只临时收紧限流或转人工复核，**不放宽**固定硬配额，不自动永久封禁（§10.2、§10.5）；
- 可撤销：``revoke_subject`` 支持误判恢复，窗口过期自动回到 normal（§10.5）；
- 最小记录：不保存请求体、Cookie、Authorization、原始 IP 与原始查询串（§10.2、§10.3）。

本模块不读写任何真实业务对象；蜜罐处理器由 router 直接返回固定响应。
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import cache
from app.core.audit import record_audit
from app.core.enums import (
    CacheClass,
    DefenseMode,
    DefenseSignalKind,
    HoneytokenStatus,
    RiskAction,
    RiskLevel,
)
from app.core.errors import InvalidInput, NotFound
from app.core.security import generate_token, token_digest
from app.modules.defense.models import DefenseEvent, DefensePolicy, Honeytoken, RiskSubject

__all__ = [
    "AUDIT_ACTION_CHANGED",
    "AUDIT_HONEYTOKEN",
    "AUDIT_REVOKED",
    "AUDIT_SIGNAL",
    "PolicyView",
    "cleanup_expired",
    "create_honeytoken",
    "evaluate_subject",
    "factor_for",
    "list_events",
    "list_honeytokens",
    "list_policies",
    "list_subjects",
    "path_template",
    "pseudonymize",
    "record_honeypot_hit",
    "record_signal",
    "register_policy",
    "resolve_subject_key",
    "retire_honeytoken",
    "revoke_subject",
    "subject_key",
    "trigger_honeytoken",
]

AUDIT_SIGNAL = "defense.signal"
AUDIT_ACTION_CHANGED = "defense.action_changed"
AUDIT_REVOKED = "defense.revoked"
AUDIT_HONEYTOKEN = "defense.honeytoken"

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUM_RE = re.compile(r"/\d+(?=/|$)")

# 单主体窗口事件上限的进程内计数，避免每次都 count(*)；权威上限仍以数据库计数复核。
_event_counter: dict[str, tuple[float, int]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def now_utc() -> datetime:
    """公开的当前时间（路由与测试使用），统一 UTC 带时区。"""
    return _now()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pseudonymize(value: str) -> str:
    """假名化不可逆标识（IP 等），只保留足够关联同一主体的前缀。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def subject_key(kind: str, value: str) -> str:
    """统一主体键格式：``u:``/``s:`` 用内部 ID，``ip:`` 用假名。"""
    if kind == "ip":
        return f"ip:{pseudonymize(value)}"
    return f"{kind}:{value}"


def resolve_subject_key(
    user: Any, session: Any, request: Any
) -> tuple[str, str]:
    """与限流一致的主体优先级：登录用户 → 会话 → 来源 IP（pachong.md §四）。"""
    if user is not None:
        return "user", subject_key("user", str(user.id))
    if session is not None:
        return "session", subject_key("session", str(session.id))
    client_ip = request.client.host if getattr(request, "client", None) else None
    return "ip", subject_key("ip", client_ip or "unknown")


def path_template(path: str) -> str:
    """把路径中的 UUID 与数字段替换为占位符，避免把真实对象 ID 写入事件。"""
    if not path:
        return ""
    cleaned = _UUID_RE.sub("{id}", path)
    cleaned = _NUM_RE.sub("/{n}", cleaned)
    return cleaned[:200]


@dataclass(frozen=True)
class PolicyView:
    """当前生效策略（数据库有效版本优先，否则取 settings 默认值）。"""

    mode: str
    suspicious_score: float
    high_score: float
    throttle_factor_suspicious: float
    throttle_factor_high: float
    window_seconds: int
    decay_half_life_seconds: int
    action_ttl_seconds: int
    source: str  # "settings" | "policy:<version>"


def _settings_policy() -> PolicyView:
    return PolicyView(
        mode=settings.DEFENSE_MODE,
        suspicious_score=float(settings.DEFENSE_SUSPICIOUS_SCORE),
        high_score=float(settings.DEFENSE_HIGH_SCORE),
        throttle_factor_suspicious=float(settings.DEFENSE_THROTTLE_FACTOR_SUSPICIOUS),
        throttle_factor_high=float(settings.DEFENSE_THROTTLE_FACTOR_HIGH),
        window_seconds=int(settings.DEFENSE_WINDOW_SECONDS),
        decay_half_life_seconds=int(settings.DEFENSE_DECAY_HALF_LIFE_SECONDS),
        action_ttl_seconds=int(settings.DEFENSE_ACTION_TTL_SECONDS),
        source="settings",
    )


async def active_policy(db: AsyncSession) -> PolicyView:
    """取最新有效策略版本；没有有效行时回落到 settings，不因缺表/缺行阻塞请求。"""
    if not settings.DEFENSE_ENABLED:
        base = _settings_policy()
        return PolicyView(**{**base.__dict__, "mode": DefenseMode.OFF.value})
    now = _now()
    stmt = (
        select(DefensePolicy)
        .where(
            DefensePolicy.enabled.is_(True),
            DefensePolicy.valid_from.is_(None) | (DefensePolicy.valid_from <= now),
            DefensePolicy.valid_until.is_(None) | (DefensePolicy.valid_until > now),
        )
        .order_by(DefensePolicy.version.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return _settings_policy()
    return PolicyView(
        mode=row.mode,
        suspicious_score=float(row.suspicious_score),
        high_score=float(row.high_score),
        throttle_factor_suspicious=float(row.throttle_factor_suspicious),
        throttle_factor_high=float(row.throttle_factor_high),
        window_seconds=int(row.window_seconds),
        decay_half_life_seconds=int(row.decay_half_life_seconds),
        action_ttl_seconds=int(row.action_ttl_seconds),
        source=f"policy:{row.version}",
    )


def decayed_score(
    events: list[tuple[float, datetime]], *, now: datetime, half_life_seconds: int
) -> float:
    """按半衰期指数衰减求和；越久远的信号影响越小（§10.5）。"""
    half_life = max(1, half_life_seconds)
    total = 0.0
    for weight, occurred in events:
        age = max(0.0, (now - occurred).total_seconds())
        total += weight * math.pow(0.5, age / half_life)
    return round(total, 2)


def _level_for(score: float, policy: PolicyView) -> str:
    if score >= policy.high_score:
        return RiskLevel.HIGH.value
    if score >= policy.suspicious_score:
        return RiskLevel.SUSPICIOUS.value
    return RiskLevel.NORMAL.value


def _action_for(level: str, policy: PolicyView) -> str:
    """观察模式只记录等级；enforce 下才收紧限流或转人工复核。"""
    if policy.mode != DefenseMode.ENFORCE.value:
        return RiskAction.OBSERVE.value
    if level == RiskLevel.HIGH.value:
        return RiskAction.REVIEW.value
    if level == RiskLevel.SUSPICIOUS.value:
        return RiskAction.THROTTLE.value
    return RiskAction.OBSERVE.value


def _factor_for_action(action: str, policy: PolicyView) -> float:
    if action == RiskAction.REVIEW.value:
        return policy.throttle_factor_high
    if action == RiskAction.THROTTLE.value:
        return policy.throttle_factor_suspicious
    return 1.0


async def record_signal(
    db: AsyncSession,
    *,
    kind: DefenseSignalKind,
    signal: str,
    weight: int,
    subject_kind: str,
    subject: str,
    request_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    decoy_label: str | None = None,
    detail: dict[str, Any] | None = None,
    evaluate: bool = True,
) -> RiskSubject | None:
    """记录一条风险信号并（可选）重新评估主体。

    单主体单窗口事件数达到上限后**不再逐条落库**，只累计 ``signal_count``，
    避免高频触发把审计与事件表拖垮（§10.2、§10.5）。
    """
    if not settings.DEFENSE_ENABLED or settings.DEFENSE_MODE == DefenseMode.OFF.value:
        return None

    now = _now()
    policy = await active_policy(db)
    window_start = now - timedelta(seconds=policy.window_seconds)

    existing = (
        await db.execute(select(RiskSubject).where(RiskSubject.subject_key == subject))
    ).scalar_one_or_none()

    # 进程内预筛 + 数据库计数复核；计数只在本窗口内有效。
    cache_key = f"{subject}:{int(window_start.timestamp()) // policy.window_seconds}"
    cached_at, cached_count = _event_counter.get(cache_key, (0.0, 0))
    if time.monotonic() - cached_at > policy.window_seconds:
        cached_count = 0
    if cached_count < settings.DEFENSE_MAX_EVENTS_PER_SUBJECT_WINDOW:
        db_count = (
            await db.execute(
                select(sa_func.count(DefenseEvent.id)).where(
                    DefenseEvent.subject_key == subject,
                    DefenseEvent.occurred_at >= window_start,
                )
            )
        ).scalar_one()
        if int(db_count) >= settings.DEFENSE_MAX_EVENTS_PER_SUBJECT_WINDOW:
            cached_count = settings.DEFENSE_MAX_EVENTS_PER_SUBJECT_WINDOW
        else:
            db.add(
                DefenseEvent(
                    kind=kind.value,
                    signal=signal[:60],
                    weight=max(1, min(100, int(weight))),
                    subject_kind=subject_kind,
                    subject_key=subject,
                    request_id=request_id,
                    method=method,
                    path_template=path_template(path or ""),
                    decoy_label=decoy_label,
                    detail=detail or None,
                    occurred_at=now,
                )
            )
            cached_count += 1
    _event_counter[cache_key] = (time.monotonic(), cached_count)

    if existing is None:
        existing = RiskSubject(
            subject_key=subject,
            subject_kind=subject_kind,
            score=0.0,
            level=RiskLevel.NORMAL.value,
            action=RiskAction.OBSERVE.value,
            window_started_at=window_start,
            signal_count=0,
            revision=1,
        )
        db.add(existing)
        await db.flush()

    existing.signal_count = int(existing.signal_count or 0) + 1
    existing.last_signal_at = now
    existing.last_signal_kind = kind.value
    existing.revision = int(existing.revision or 1) + 1

    result: RiskSubject | None = existing
    if evaluate:
        result = await evaluate_subject(db, subject=existing, policy=policy, now=now)

    cache.invalidate(f"defense:risk:{subject}")
    return result


async def evaluate_subject(
    db: AsyncSession,
    *,
    subject: RiskSubject,
    policy: PolicyView | None = None,
    now: datetime | None = None,
) -> RiskSubject:
    """按窗口内信号重算分数、等级与处置；处置带有效期，观察模式不改业务结果。"""
    policy = policy or await active_policy(db)
    now = now or _now()
    window_start = now - timedelta(seconds=policy.window_seconds)

    rows = (
        await db.execute(
            select(DefenseEvent.weight, DefenseEvent.occurred_at).where(
                DefenseEvent.subject_key == subject.subject_key,
                DefenseEvent.occurred_at >= window_start,
            )
        )
    ).all()
    events = [(float(weight), _aware(occurred) or now) for weight, occurred in rows]

    # 撤销后重新计数：撤销只清除当前处置，不删除历史事件（历史仍可复盘）。
    if subject.revoked_at is not None:
        events = [item for item in events if item[1] >= (_aware(subject.revoked_at) or now)]

    score = decayed_score(
        events, now=now, half_life_seconds=policy.decay_half_life_seconds
    )
    level = _level_for(score, policy)
    action = _action_for(level, policy)

    previous_action = subject.action
    subject.score = score
    subject.level = level
    subject.action = action
    subject.window_started_at = window_start
    if action == RiskAction.OBSERVE.value:
        subject.action_expires_at = None
        subject.action_reason = None
    else:
        subject.action_expires_at = now + timedelta(seconds=policy.action_ttl_seconds)
        subject.action_reason = (
            "窗口内多类异常信号累积，临时收紧该主体调用速率并转人工复核"
            if action == RiskAction.REVIEW.value
            else "窗口内异常信号累积，临时收紧该主体调用速率"
        )

    if action != previous_action:
        await record_audit(
            db,
            action=AUDIT_ACTION_CHANGED,
            object_type="risk_subject",
            object_id=subject.id,
            object_version=subject.revision,
            before={"action": previous_action},
            after={
                "action": action,
                "level": level,
                "score": score,
                "mode": policy.mode,
                "policy": policy.source,
            },
            reason="风险等级变化",
        )
    await db.flush()
    return subject


async def factor_for(
    db: AsyncSession, *, kind: str, subject: str
) -> float:
    """返回该主体的限流折扣系数（1.0 表示不收紧）。

    只在 ``enforce`` 模式且处置未过期、未撤销时返回小于 1 的系数；
    任何异常都回落 1.0，检测不可用不影响正常业务（§10.5）。
    """
    if not settings.DEFENSE_ENABLED or settings.DEFENSE_MODE != DefenseMode.ENFORCE.value:
        return 1.0
    key = subject_key(kind, subject)

    async def _load() -> tuple[str, float | None]:
        row = (
            await db.execute(select(RiskSubject).where(RiskSubject.subject_key == key))
        ).scalar_one_or_none()
        if row is None:
            return RiskAction.OBSERVE.value, None
        return row.action, _aware(row.action_expires_at)

    try:
        action, expires_at = await cache.get_or_set(
            f"defense:risk:{key}",
            _load,
            cache_class=CacheClass.VIEWER_SCOPED,
            ttl=15,
            prefix=f"defense:risk:{key}",
        )
    except Exception:  # noqa: BLE001 - 检测不可用不影响正常业务
        return 1.0

    if action == RiskAction.OBSERVE.value:
        return 1.0
    if expires_at is not None and expires_at <= _now():
        return 1.0
    policy = await active_policy(db)
    return _factor_for_action(action, policy)


async def revoke_subject(
    db: AsyncSession,
    *,
    subject_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    reason: str,
    request_id: str | None = None,
) -> RiskSubject:
    """撤销误判：清除当前处置并重置窗口计数，保留历史事件用于复盘（§10.5）。"""
    row = (
        await db.execute(select(RiskSubject).where(RiskSubject.id == subject_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("风险主体不存在")
    if row.revoked_at is not None:
        return row

    before = {"action": row.action, "level": row.level, "score": row.score}
    row.revoked_at = _now()
    row.revoked_by = actor_id
    row.revoke_reason = reason[:200]
    row.action = RiskAction.OBSERVE.value
    row.action_expires_at = None
    row.action_reason = None
    row.level = RiskLevel.NORMAL.value
    row.score = 0.0
    row.signal_count = 0
    row.window_started_at = row.revoked_at
    row.revision = int(row.revision or 1) + 1
    await record_audit(
        db,
        action=AUDIT_REVOKED,
        object_type="risk_subject",
        object_id=row.id,
        object_version=row.revision,
        actor_id=actor_id,
        before=before,
        after={"action": row.action, "level": row.level},
        reason=reason,
        request_id=request_id,
    )
    cache.invalidate(f"defense:risk:{row.subject_key}")
    await db.flush()
    return row


async def record_honeypot_hit(
    db: AsyncSession,
    *,
    subject_kind: str,
    subject: str,
    request_id: str | None,
    method: str | None,
    path: str,
    decoy_label: str,
) -> None:
    """蜜罐命中：只记录信号，不读取业务数据、不执行传入内容（§10.2）。"""
    await record_signal(
        db,
        kind=DefenseSignalKind.HONEYPOT_HIT,
        signal="honeypot_path_access",
        weight=45,
        subject_kind=subject_kind,
        subject=subject,
        request_id=request_id,
        method=method,
        path=path,
        decoy_label=decoy_label,
        detail={"decoy": "path", "status": 404},
    )


async def trigger_honeytoken(
    db: AsyncSession,
    *,
    raw_token: str,
    subject_kind: str,
    subject: str,
    request_id: str | None,
    method: str | None,
    path: str,
) -> str | None:
    """蜜罐标记触发：返回登记的可读标识（未知标记返回 None）。

    标记本身没有任何业务权限；未知标记也记录，说明有人在试探或复用了已失效标记。
    """
    digest = token_digest(raw_token)
    row = (
        await db.execute(select(Honeytoken).where(Honeytoken.token_digest == digest))
    ).scalar_one_or_none()

    now = _now()
    label: str | None = None
    weight = 40
    if row is not None:
        label = row.label
        active = (
            row.status == HoneytokenStatus.ACTIVE.value
            and (row.expires_at is None or (_aware(row.expires_at) or now) > now)
        )
        weight = 70 if active else 55
        if row.status == HoneytokenStatus.ACTIVE.value and not active:
            row.status = HoneytokenStatus.RETIRED.value
            row.retired_at = now

    await record_signal(
        db,
        kind=DefenseSignalKind.HONEYTOKEN_HIT,
        signal="honeytoken_use" if row is not None else "unknown_token_use",
        weight=weight,
        subject_kind=subject_kind,
        subject=subject,
        request_id=request_id,
        method=method,
        path=path,
        decoy_label=label,
        detail={"registered": row is not None, "active": weight == 70},
    )
    return label


async def create_honeytoken(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    label: str,
    placement: str,
    placement_ref: str | None,
    expected_visitor: str | None,
    expires_at: datetime | None,
    note: str | None,
    request_id: str | None = None,
) -> tuple[Honeytoken, str]:
    """登记一个蜜罐标记，明文只在此处返回一次，库里只存摘要。"""
    if not label.strip():
        raise InvalidInput("标记名称不能为空")
    if placement not in {"isolated_material", "static_decoy", "test_fixture"}:
        raise InvalidInput("投放位置只能是 isolated_material / static_decoy / test_fixture")

    raw = generate_token(24)
    row = Honeytoken(
        token_digest=token_digest(raw),
        label=label.strip()[:64],
        placement=placement,
        placement_ref=(placement_ref or None),
        owner_id=actor_id,
        expected_visitor=(expected_visitor or None),
        status=HoneytokenStatus.ACTIVE.value,
        expires_at=expires_at,
        note=(note or None),
    )
    db.add(row)
    await record_audit(
        db,
        action=AUDIT_HONEYTOKEN,
        object_type="honeytoken",
        object_id=row.id,
        actor_id=actor_id,
        after={"label": row.label, "placement": row.placement},
        reason="登记蜜罐标记",
        request_id=request_id,
    )
    await db.flush()
    return row, raw


async def retire_honeytoken(
    db: AsyncSession,
    *,
    token_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    reason: str,
    request_id: str | None = None,
) -> Honeytoken:
    row = (
        await db.execute(select(Honeytoken).where(Honeytoken.id == token_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("蜜罐标记不存在")
    if row.status == HoneytokenStatus.RETIRED.value:
        return row
    row.status = HoneytokenStatus.RETIRED.value
    row.retired_at = _now()
    await record_audit(
        db,
        action=AUDIT_HONEYTOKEN,
        object_type="honeytoken",
        object_id=row.id,
        actor_id=actor_id,
        before={"status": HoneytokenStatus.ACTIVE.value},
        after={"status": row.status},
        reason=reason,
        request_id=request_id,
    )
    await db.flush()
    return row


async def register_policy(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    mode: str,
    suspicious_score: float,
    high_score: float,
    throttle_factor_suspicious: float,
    throttle_factor_high: float,
    window_seconds: int,
    decay_half_life_seconds: int,
    action_ttl_seconds: int,
    valid_from: datetime | None,
    valid_until: datetime | None,
    note: str | None,
    request_id: str | None = None,
) -> DefensePolicy:
    """新增策略版本；不修改旧版本，历史阈值可追溯（§10.5）。"""
    if mode not in {"observe", "enforce"}:
        raise InvalidInput("模式只能是 observe 或 enforce")
    if high_score <= suspicious_score:
        raise InvalidInput("高风险阈值必须大于可疑阈值")
    for name, value in (
        ("throttle_factor_suspicious", throttle_factor_suspicious),
        ("throttle_factor_high", throttle_factor_high),
    ):
        if not 0 < value <= 1:
            raise InvalidInput(f"{name} 必须在 (0, 1] 区间")
    if valid_from and valid_until and valid_until <= valid_from:
        raise InvalidInput("失效时间必须晚于生效时间")

    latest = (
        await db.execute(select(sa_func.max(DefensePolicy.version)))
    ).scalar_one_or_none() or 0
    row = DefensePolicy(
        version=int(latest) + 1,
        mode=mode,
        suspicious_score=float(suspicious_score),
        high_score=float(high_score),
        throttle_factor_suspicious=float(throttle_factor_suspicious),
        throttle_factor_high=float(throttle_factor_high),
        window_seconds=int(window_seconds),
        decay_half_life_seconds=int(decay_half_life_seconds),
        action_ttl_seconds=int(action_ttl_seconds),
        enabled=True,
        valid_from=valid_from,
        valid_until=valid_until,
        created_by=actor_id,
        note=(note or None),
    )
    db.add(row)
    await record_audit(
        db,
        action=AUDIT_ACTION_CHANGED,
        object_type="defense_policy",
        object_id=row.id,
        object_version=row.version,
        actor_id=actor_id,
        after={
            "version": row.version,
            "mode": mode,
            "suspicious_score": row.suspicious_score,
            "high_score": row.high_score,
        },
        reason="新增风险策略版本",
        request_id=request_id,
    )
    await db.flush()
    return row


async def list_events(
    db: AsyncSession,
    *,
    kind: str | None = None,
    subject: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DefenseEvent], int]:
    stmt = select(DefenseEvent)
    count_stmt = select(sa_func.count(DefenseEvent.id))
    if kind:
        stmt = stmt.where(DefenseEvent.kind == kind)
        count_stmt = count_stmt.where(DefenseEvent.kind == kind)
    if subject:
        stmt = stmt.where(DefenseEvent.subject_key == subject)
        count_stmt = count_stmt.where(DefenseEvent.subject_key == subject)
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        (
            await db.execute(
                stmt.order_by(DefenseEvent.occurred_at.desc())
                .limit(max(1, min(200, limit)))
                .offset(max(0, offset))
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def list_subjects(
    db: AsyncSession,
    *,
    level: str | None = None,
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[RiskSubject], int]:
    stmt = select(RiskSubject)
    count_stmt = select(sa_func.count(RiskSubject.id))
    if level:
        stmt = stmt.where(RiskSubject.level == level)
        count_stmt = count_stmt.where(RiskSubject.level == level)
    if action:
        stmt = stmt.where(RiskSubject.action == action)
        count_stmt = count_stmt.where(RiskSubject.action == action)
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        (
            await db.execute(
                stmt.order_by(RiskSubject.score.desc(), RiskSubject.last_signal_at.desc())
                .limit(max(1, min(200, limit)))
                .offset(max(0, offset))
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def list_honeytokens(
    db: AsyncSession, *, status: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[Honeytoken], int]:
    stmt = select(Honeytoken)
    count_stmt = select(sa_func.count(Honeytoken.id))
    if status:
        stmt = stmt.where(Honeytoken.status == status)
        count_stmt = count_stmt.where(Honeytoken.status == status)
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        (
            await db.execute(
                stmt.order_by(Honeytoken.created_at.desc())
                .limit(max(1, min(200, limit)))
                .offset(max(0, offset))
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def list_policies(
    db: AsyncSession, *, limit: int = 50
) -> list[DefensePolicy]:
    rows = (
        (
            await db.execute(
                select(DefensePolicy)
                .order_by(DefensePolicy.version.desc())
                .limit(max(1, min(100, limit)))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def cleanup_expired(db: AsyncSession, *, retention_days: int | None = None) -> int:
    """清理超过保留期的事件，并把过期标记置为 retired；只删防御事件，不动业务表。"""
    from sqlalchemy import delete, update

    days = max(1, int(retention_days or settings.DEFENSE_EVENT_RETENTION_DAYS))
    cutoff = _now() - timedelta(days=days)
    result = await db.execute(
        delete(DefenseEvent)
        .where(DefenseEvent.occurred_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    now = _now()
    await db.execute(
        update(Honeytoken)
        .where(
            Honeytoken.status == HoneytokenStatus.ACTIVE.value,
            Honeytoken.expires_at.is_not(None),
            Honeytoken.expires_at <= now,
        )
        .values(status=HoneytokenStatus.RETIRED.value, retired_at=now)
        .execution_options(synchronize_session=False)
    )
    _event_counter.clear()
    return int(result.rowcount or 0)
