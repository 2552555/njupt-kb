"""TOTP (RFC 6238), one-use recovery codes, and session step-up.

The independent Fernet key must be backed up and rotated with the encrypted seeds.
Losing the authenticator and all recovery codes has no email-only bypass.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.errors import (
    Conflict,
    DependencyUnavailable,
    Forbidden,
    NotEnabled,
    Unauthenticated,
)
from app.core.security import generate_token, token_digest, verify_password
from app.modules.identity.models import Session, User, UserMFA


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _cipher() -> Fernet:
    if not settings.MFA_ENCRYPTION_KEY:
        raise NotEnabled("验证器加密密钥尚未配置")
    try:
        return Fernet(settings.MFA_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise DependencyUnavailable("验证器加密配置不可用") from exc


def totp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)


def _counter(secret: str, code: str, previous: int | None) -> int | None:
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current = int(time.time()) // 30
    for counter in (current, current - 1, current + 1):
        if counter > (previous if previous is not None else -1) and hmac.compare_digest(
            totp(secret, counter), code
        ):
            return counter
    return None


def _secret(row: UserMFA) -> str:
    try:
        return _cipher().decrypt(row.secret_ciphertext.encode()).decode()
    except (InvalidToken, AttributeError, UnicodeError) as exc:
        raise DependencyUnavailable("验证器密钥不可用，请联系受权运维人员") from exc


async def _lock_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.status != "active":
        raise Forbidden("当前账号不能管理验证器")
    return user


async def verify(db: AsyncSession, user_id: uuid.UUID, code: str | None) -> bool:
    """Caller holds the user row lock. CAS also rejects replay under concurrent use."""
    row = await db.get(UserMFA, user_id, populate_existing=True)
    if row is None or row.enabled_at is None:
        return False
    if not code:
        raise Unauthenticated("请输入验证器代码或一次性恢复码", code="MFA_REQUIRED")
    code = code.strip().lower().replace("-", "")
    counter = _counter(_secret(row), code, row.last_counter)
    if counter is not None:
        result = await db.execute(
            update(UserMFA)
            .where(
                UserMFA.user_id == user_id,
                UserMFA.last_counter == row.last_counter,
            )
            .values(last_counter=counter)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise Unauthenticated("该验证器代码已使用", code="MFA_INVALID")
        await db.refresh(row)
        return True
    digest = token_digest(code)
    matched = next(
        (value for value in row.recovery_hashes if hmac.compare_digest(value, digest)),
        None,
    )
    if matched is None:
        raise Unauthenticated("验证器代码或恢复码无效，或已使用", code="MFA_INVALID")
    # User row lock serializes recovery-code consumption on PostgreSQL.
    result = await db.execute(
        update(UserMFA)
        .where(
            UserMFA.user_id == user_id,
            UserMFA.revision == row.revision,
        )
        .values(
            recovery_hashes=[
                value for value in row.recovery_hashes if value != matched
            ],
            revision=row.revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise Unauthenticated("该恢复码已使用，请刷新后重试", code="MFA_INVALID")
    await db.refresh(row)
    return True


async def status(db: AsyncSession, user: User, session: Session) -> dict:
    row = await db.get(UserMFA, user.id)
    verified = session.mfa_verified_at
    return {
        "enabled": row is not None and row.enabled_at is not None,
        "configured": bool(settings.MFA_ENCRYPTION_KEY),
        "recovery_remaining": len(row.recovery_hashes) if row and row.enabled_at else 0,
        "recently_verified": verified is not None
        and (_now() - _aware(verified)).total_seconds()
        <= settings.MFA_REAUTH_MAX_AGE_SECONDS,
    }


async def begin_setup(
    db: AsyncSession, user: User, password: str, request_id: str
) -> dict:
    user = await _lock_user(db, user.id)
    if not verify_password(password, user.password_hash):
        raise Forbidden("密码不正确")
    row = await db.get(UserMFA, user.id)
    if row and row.enabled_at:
        raise Conflict("验证器已启用，请使用现有凭据先禁用后再换绑")
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    encrypted = _cipher().encrypt(secret.encode()).decode()
    if row is None:
        row = UserMFA(user_id=user.id)
        db.add(row)
    row.secret_ciphertext = encrypted
    row.setup_expires_at = _now() + timedelta(minutes=10)
    row.last_counter = None
    row.recovery_hashes = []
    await db.flush()
    await record_audit(
        db,
        action="identity.mfa_setup_started",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        request_id=request_id,
    )
    uri = (
        "otpauth://totp/"
        + quote(f"{settings.APP_NAME}:{user.email}", safe="")
        + "?"
        + urlencode(
            {
                "secret": secret,
                "issuer": settings.APP_NAME,
                "algorithm": "SHA1",
                "digits": 6,
                "period": 30,
            }
        )
    )
    return {"secret": secret, "otpauth_uri": uri}


async def _rotate_session(
    db: AsyncSession, user_id: uuid.UUID, session: Session, verified: bool
) -> str:
    now = _now()
    session = await db.scalar(
        select(Session)
        .where(Session.id == session.id, Session.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None or session.revoked_at or _aware(session.expires_at) <= now:
        raise Unauthenticated("会话已失效，请重新登录")
    await db.execute(
        update(Session)
        .where(
            Session.user_id == user_id,
            Session.id != session.id,
            Session.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason="mfa_changed")
        .execution_options(synchronize_session=False)
    )
    raw = generate_token()
    session.token_digest = token_digest(raw)
    session.mfa_verified_at = now if verified else None
    await db.flush()
    return raw


async def confirm_setup(
    db: AsyncSession, user: User, session: Session, code: str, request_id: str
) -> tuple[list[str], str]:
    await _lock_user(db, user.id)
    row = await db.get(UserMFA, user.id, populate_existing=True)
    if (
        not row
        or row.enabled_at
        or not row.setup_expires_at
        or _aware(row.setup_expires_at) <= _now()
    ):
        raise Conflict("绑定请求不存在、已完成或已过期，请重新开始")
    counter = _counter(_secret(row), code.strip(), None)
    if counter is None:
        raise Unauthenticated("验证器代码无效", code="MFA_INVALID")
    codes = [secrets.token_hex(10) for _ in range(8)]
    row.recovery_hashes = [token_digest(code) for code in codes]
    row.enabled_at = _now()
    row.setup_expires_at = None
    row.last_counter = counter
    raw = await _rotate_session(db, user.id, session, True)
    await record_audit(
        db,
        action="identity.mfa_enabled",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        request_id=request_id,
    )
    return codes, raw


async def reauthenticate(
    db: AsyncSession,
    user: User,
    session: Session,
    password: str,
    code: str,
    request_id: str,
) -> str:
    user = await _lock_user(db, user.id)
    if not verify_password(password, user.password_hash):
        raise Unauthenticated("密码或验证器代码不正确")
    if not await verify(db, user.id, code):
        raise Forbidden("请先启用验证器", code="MFA_SETUP_REQUIRED")
    raw = await _rotate_session(db, user.id, session, True)
    await record_audit(
        db,
        action="identity.mfa_reauthenticated",
        object_type="session",
        object_id=session.id,
        actor_id=user.id,
        request_id=request_id,
    )
    return raw


async def disable(
    db: AsyncSession,
    user: User,
    session: Session,
    password: str,
    code: str,
    request_id: str,
) -> str:
    raw = await reauthenticate(db, user, session, password, code, request_id)
    row = await db.get(UserMFA, user.id)
    row.secret_ciphertext = None
    row.enabled_at = None
    row.last_counter = None
    row.recovery_hashes = []
    session.mfa_verified_at = None
    await record_audit(
        db,
        action="identity.mfa_disabled",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        request_id=request_id,
    )
    return raw


async def require_recent(db: AsyncSession, user: User, session: Session) -> None:
    row = await db.get(UserMFA, user.id)
    if row is None or row.enabled_at is None:
        raise Forbidden("敏感管理操作需要先启用验证器", code="MFA_SETUP_REQUIRED")
    if (
        session.mfa_verified_at is None
        or (_now() - _aware(session.mfa_verified_at)).total_seconds()
        > settings.MFA_REAUTH_MAX_AGE_SECONDS
    ):
        raise Forbidden(
            "请在账号安全页面重新验证密码和验证器代码", code="MFA_REAUTH_REQUIRED"
        )
