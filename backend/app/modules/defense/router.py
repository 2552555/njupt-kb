"""defense 路由：蜜罐入口、标记触发与管理端查询/处置。

- 蜜罐与标记入口**不提供任何业务能力**，只返回固定 404 并记录信号（AI 对抗 §10.2、§10.3）；
- 管理端接口统一要求 `defense.manage`；策略变更是敏感写操作，生产环境要求近期 MFA；
- 前缀由 `app/main.py` 统一加 `/api/v1`，蜜罐路径单独按配置注册，不进入 OpenAPI。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import (
    get_current_user,
    get_db,
    get_optional_user,
    get_request_id,
)
from app.core.enums import HoneytokenStatus, RiskLevel
from app.core.errors import error_body
from app.core.permissions import DEFENSE_MANAGE, require
from app.modules.defense import service
from app.modules.defense.models import DefenseEvent, Honeytoken, RiskSubject
from app.modules.defense.schemas import (
    DefenseEventOut,
    DefenseOverviewOut,
    HoneytokenCreateIn,
    HoneytokenCreatedOut,
    HoneytokenOut,
    PolicyCreateIn,
    PolicyOut,
    RevokeIn,
    RiskSubjectOut,
)
from app.modules.ratelimit.deps import get_optional_session

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["defense"])

logger = logging.getLogger("app.csp")

CSP_REPORT_MAX_BYTES = 8192

# 蜜罐处理器单独注册，路径与响应完全固定（见 main.py）。
honeypot_router = APIRouter(tags=["internal"])

# 蜜罐的进程内并发与事件记录上限：诱饵被高频触发时聚合处理，不拖垮平台（§10.2）。
_honeypot_semaphore: asyncio.Semaphore | None = None
_honeypot_events: deque[float] = deque(maxlen=2048)
# CSP 报告是浏览器自动重发的：只做进程级上限并丢弃，避免报告风暴拖垮日志。
_csp_events: deque[float] = deque(maxlen=4096)


def _csp_budget_ok() -> bool:
    now = time.monotonic()
    while _csp_events and now - _csp_events[0] > 60:
        _csp_events.popleft()
    if len(_csp_events) >= 300:
        return False
    _csp_events.append(now)
    return True


def _fixed_not_found(request_id: str) -> JSONResponse:
    """固定、短小、无敏感信息的 404；不透露诱饵或真实路由是否存在。"""
    return JSONResponse(
        status_code=404,
        content=error_body("NOT_FOUND", "资源不存在", request_id, None),
    )


def _honeypot_budget_ok() -> bool:
    """每分钟事件记录上限；超出只丢弃记录，不改变响应。"""
    now = time.monotonic()
    while _honeypot_events and now - _honeypot_events[0] > 60:
        _honeypot_events.popleft()
    if len(_honeypot_events) >= settings.HONEYPOT_MAX_EVENTS_PER_MINUTE:
        return False
    _honeypot_events.append(now)
    return True


async def honeypot_handler(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: "User | None" = Depends(get_optional_user),
    session: object | None = Depends(get_optional_session),
) -> JSONResponse:
    """诱饵路径：不读写业务数据、不执行传入内容、不签发链接。"""
    global _honeypot_semaphore
    request_id = get_request_id(request)
    if not settings.HONEYPOT_ENABLED:
        return _fixed_not_found(request_id)

    # 请求体大小上限：不读取正文，只按声明的长度拒绝记录（避免把大文件读进内存）。
    try:
        declared = int(request.headers.get("Content-Length") or 0)
    except ValueError:
        declared = 0
    oversized = declared > settings.HONEYPOT_MAX_BODY_BYTES

    if _honeypot_semaphore is None:
        _honeypot_semaphore = asyncio.Semaphore(8)
    try:
        async with _honeypot_semaphore:
            if not oversized and _honeypot_budget_ok():
                kind, subject = service.resolve_subject_key(user, session, request)
                try:
                    await service.record_honeypot_hit(
                        db,
                        subject_kind=kind,
                        subject=subject,
                        request_id=request_id,
                        method=request.method,
                        path=settings.HONEYPOT_PATH,
                        decoy_label="internal-debug-export",
                    )
                    await db.commit()
                except Exception:  # noqa: BLE001 - 诱饵记录失败不影响固定响应
                    await db.rollback()
    except asyncio.TimeoutError:  # pragma: no cover - 信号量等待超时
        pass
    return _fixed_not_found(request_id)


@router.get("/defense/tokens/{token}", include_in_schema=False)
async def honeytoken_entry(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: "User | None" = Depends(get_optional_user),
    session: object | None = Depends(get_optional_session),
) -> JSONResponse:
    """蜜罐标记触发入口：标记没有业务权限，命中只记录信号并返回固定 404。"""
    request_id = get_request_id(request)
    if not settings.DEFENSE_ENABLED or not token or len(token) > 128:
        return _fixed_not_found(request_id)
    kind, subject = service.resolve_subject_key(user, session, request)
    try:
        await service.trigger_honeytoken(
            db,
            raw_token=token,
            subject_kind=kind,
            subject=subject,
            request_id=request_id,
            method=request.method,
            path="/api/v1/defense/tokens/{token}",
        )
        await db.commit()
    except Exception:  # noqa: BLE001 - 记录失败不影响固定响应
        await db.rollback()
    return _fixed_not_found(request_id)


@router.post("/csp/reports", status_code=204, include_in_schema=False)
async def csp_reports(request: Request) -> Response:
    """接收浏览器的 CSP 违规报告，只记录指令与来源，不保存正文或完整 URL。

    报告端点无法要求 CSRF 令牌（浏览器自动发送），因此按来源限流、限制体积，
    并且只写日志、不落库，避免被刷爆事件表。
    """
    raw = await request.body()
    if not raw or len(raw) > CSP_REPORT_MAX_BYTES or not _csp_budget_ok():
        return Response(status_code=204)

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return Response(status_code=204)

    reports: list[dict] = []
    if isinstance(payload, dict) and "csp-report" in payload:
        legacy = payload["csp-report"]
        if isinstance(legacy, dict):
            reports.append(
                {
                    "directive": legacy.get("violated-directive") or legacy.get("effective-directive"),
                    "blocked": legacy.get("blocked-uri"),
                    "document": legacy.get("document-uri"),
                    "disposition": legacy.get("disposition"),
                }
            )
    elif isinstance(payload, list):
        for item in payload[:10]:
            if isinstance(item, dict) and isinstance(item.get("body"), dict):
                body = item["body"]
                reports.append(
                    {
                        "directive": body.get("effectiveDirective") or body.get("violatedDirective"),
                        "blocked": body.get("blockedURL"),
                        "document": body.get("documentURL"),
                        "disposition": body.get("disposition"),
                    }
                )
    elif isinstance(payload, dict) and isinstance(payload.get("body"), dict):
        body = payload["body"]
        reports.append(
            {
                "directive": body.get("effectiveDirective") or body.get("violatedDirective"),
                "blocked": body.get("blockedURL"),
                "document": body.get("documentURL"),
                "disposition": body.get("disposition"),
            }
        )

    for item in reports[:10]:
        logger.warning(
            "csp_violation",
            extra={
                # 只保留指令名、被拦资源的来源与文档路径；不记录查询串、Cookie 或正文。
                "directive": str(item.get("directive") or "")[:80],
                "blocked_origin": _origin_only(item.get("blocked")),
                "document_path": _path_only(item.get("document")),
                "disposition": str(item.get("disposition") or "")[:20],
            },
        )
    return Response(status_code=204)


def _origin_only(value: object) -> str:
    """把被拦资源归一为来源或关键字（inline/eval/data），丢掉路径与查询串。"""
    text = str(value or "")
    if not text:
        return ""
    if text in {"inline", "eval", "data", "blob", "wasm-eval"}:
        return text
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if not parsed.scheme:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"[:120] if parsed.netloc else parsed.scheme


def _path_only(value: object) -> str:
    """文档地址只保留路径模板，避免把查询串写进日志。"""
    text = str(value or "")
    if not text:
        return ""
    try:
        return service.path_template(urlsplit(text).path)
    except ValueError:
        return ""


@router.get("/admin/defense/overview", response_model=DefenseOverviewOut)
async def defense_overview(
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(DEFENSE_MANAGE)),
) -> DefenseOverviewOut:
    policy = await service.active_policy(db)
    since = service.now_utc() - timedelta(days=1)
    events_recent = int(
        (
            await db.execute(
                select(sa_func.count(DefenseEvent.id)).where(
                    DefenseEvent.occurred_at >= since
                )
            )
        ).scalar_one()
    )
    suspicious = int(
        (
            await db.execute(
                select(sa_func.count(RiskSubject.id)).where(
                    RiskSubject.level == RiskLevel.SUSPICIOUS.value
                )
            )
        ).scalar_one()
    )
    high = int(
        (
            await db.execute(
                select(sa_func.count(RiskSubject.id)).where(
                    RiskSubject.level == RiskLevel.HIGH.value
                )
            )
        ).scalar_one()
    )
    active_tokens = int(
        (
            await db.execute(
                select(sa_func.count(Honeytoken.id)).where(
                    Honeytoken.status == HoneytokenStatus.ACTIVE.value
                )
            )
        ).scalar_one()
    )
    return DefenseOverviewOut(
        enabled=settings.DEFENSE_ENABLED,
        mode=policy.mode,
        policy_source=policy.source,
        events_recent=events_recent,
        subjects_suspicious=suspicious,
        subjects_high=high,
        honeytokens_active=active_tokens,
    )


@router.get("/admin/defense/events")
async def defense_events(
    kind: str | None = Query(default=None, max_length=32),
    subject: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(DEFENSE_MANAGE)),
) -> dict[str, object]:
    rows, total = await service.list_events(
        db, kind=kind, subject=subject, limit=limit, offset=offset
    )
    return {
        "items": [DefenseEventOut.model_validate(row) for row in rows],
        "total": total,
    }


@router.get("/admin/defense/subjects")
async def defense_subjects(
    level: str | None = Query(default=None, max_length=20),
    action: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(DEFENSE_MANAGE)),
) -> dict[str, object]:
    rows, total = await service.list_subjects(
        db, level=level, action=action, limit=limit, offset=offset
    )
    return {
        "items": [RiskSubjectOut.model_validate(row) for row in rows],
        "total": total,
    }


@router.post("/admin/defense/subjects/{subject_id}/revoke", response_model=RiskSubjectOut)
async def revoke_risk_subject(
    subject_id: uuid.UUID,
    payload: RevokeIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(DEFENSE_MANAGE)),
    request_id: str = Depends(get_request_id),
) -> RiskSubjectOut:
    row = await service.revoke_subject(
        db,
        subject_id=subject_id,
        actor_id=actor.id,
        reason=payload.reason,
        request_id=request_id,
    )
    return RiskSubjectOut.model_validate(row)


@router.get("/admin/defense/honeytokens")
async def defense_honeytokens(
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(DEFENSE_MANAGE)),
) -> dict[str, object]:
    rows, total = await service.list_honeytokens(
        db, status=status, limit=limit, offset=offset
    )
    return {
        "items": [HoneytokenOut.model_validate(row) for row in rows],
        "total": total,
    }


@router.post(
    "/admin/defense/honeytokens", response_model=HoneytokenCreatedOut, status_code=201
)
async def create_honeytoken(
    payload: HoneytokenCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(DEFENSE_MANAGE)),
    request_id: str = Depends(get_request_id),
) -> HoneytokenCreatedOut:
    row, raw = await service.create_honeytoken(
        db,
        actor_id=actor.id,
        label=payload.label,
        placement=payload.placement,
        placement_ref=payload.placement_ref,
        expected_visitor=payload.expected_visitor,
        expires_at=payload.expires_at,
        note=payload.note,
        request_id=request_id,
    )
    return HoneytokenCreatedOut(
        token=HoneytokenOut.model_validate(row), raw_token=raw
    )


@router.post(
    "/admin/defense/honeytokens/{token_id}/retire", response_model=HoneytokenOut
)
async def retire_honeytoken(
    token_id: uuid.UUID,
    payload: RevokeIn,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(DEFENSE_MANAGE)),
    request_id: str = Depends(get_request_id),
) -> HoneytokenOut:
    row = await service.retire_honeytoken(
        db,
        token_id=token_id,
        actor_id=actor.id,
        reason=payload.reason,
        request_id=request_id,
    )
    return HoneytokenOut.model_validate(row)


@router.get("/admin/defense/policies")
async def defense_policies(
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(DEFENSE_MANAGE)),
) -> dict[str, object]:
    rows = await service.list_policies(db)
    policy = await service.active_policy(db)
    return {
        "items": [PolicyOut.model_validate(row) for row in rows],
        "active": {
            "mode": policy.mode,
            "suspicious_score": policy.suspicious_score,
            "high_score": policy.high_score,
            "window_seconds": policy.window_seconds,
            "source": policy.source,
        },
    }


@router.post("/admin/defense/policies", response_model=PolicyOut, status_code=201)
async def create_defense_policy(
    payload: PolicyCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(DEFENSE_MANAGE)),
    request_id: str = Depends(get_request_id),
) -> PolicyOut:
    row = await service.register_policy(
        db,
        actor_id=actor.id,
        mode=payload.mode,
        suspicious_score=payload.suspicious_score,
        high_score=payload.high_score,
        throttle_factor_suspicious=payload.throttle_factor_suspicious,
        throttle_factor_high=payload.throttle_factor_high,
        window_seconds=payload.window_seconds,
        decay_half_life_seconds=payload.decay_half_life_seconds,
        action_ttl_seconds=payload.action_ttl_seconds,
        valid_from=payload.valid_from,
        valid_until=payload.valid_until,
        note=payload.note,
        request_id=request_id,
    )
    return PolicyOut.model_validate(row)
