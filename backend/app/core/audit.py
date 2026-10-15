"""审计记录（设计文档 §27）。

包含操作者、操作、对象与版本、前后关键状态、原因、时间和请求 ID。
不记录密码、会话令牌、签名 URL 或完整文件正文。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "session_token",
    "csrf",
    "secret",
    "signature",
    "download_url",
    "storage_key",
}


def scrub(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return payload
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in SENSITIVE_KEYS:
            cleaned[key] = "[redacted]"
        elif isinstance(value, dict):
            cleaned[key] = scrub(value)
        else:
            cleaned[key] = value
    return cleaned


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    object_type: str,
    object_id: uuid.UUID | str | None = None,
    object_version: int | str | None = None,
    actor_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    request_id: str | None = None,
) -> None:
    """在调用方事务内追加一条审计；由 moderation 模块拥有 audit_logs 表。"""
    from app.modules.moderation.models import AuditLog

    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            object_version=str(object_version) if object_version is not None else None,
            before_state=scrub(before),
            after_state=scrub(after),
            reason=reason,
            request_id=request_id,
        )
    )
