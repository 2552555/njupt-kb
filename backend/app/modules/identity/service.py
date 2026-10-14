"""identity 业务规则（设计文档 §14、§28、§29）。

本文件是账号、身份、会话与令牌规则的唯一落点；router 只做解析与调用。
跨模块服务调用只在函数体内局部导入（docs/ARCHITECTURE.md §2）。
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import ratelimit
from app.core.audit import record_audit
from app.core.enums import (
    AccountStatus,
    AuthTokenPurpose,
    IdentityKind,
    IdentityStatus,
    Role,
)
from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidInput,
    NotFound,
    RateLimited,
    Unauthenticated,
)
from app.core.pagination import CursorPage, decode_cursor, encode_cursor, normalize_page_size
from app.core.security import (
    generate_token,
    hash_password,
    password_problem,
    token_digest,
    verify_password,
)
from app.modules.identity.models import AuthToken, Session, User, UserIdentity, UserProfile
from app.modules.identity.schemas import MeResponse, SessionItem

logger = logging.getLogger("app.identity")

# 配置中没有对应字段，故在此声明令牌有效期与失败延迟这两个业务常量。
EMAIL_VERIFICATION_TTL_HOURS = 24
PASSWORD_RESET_TTL_MINUTES = 30
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_FAILURE_DELAY_SECONDS = 1.5
# 单一 IP 限额放宽，避免校园共享网络被整体判定异常（§28）。
LOGIN_IP_LIMIT_MULTIPLIER = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _verification_link(path: str, token: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}?token={token}"


def _send_smtp(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls()
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def _deliver_mail(
    *, to: str, subject: str, body: str, log_event: str, log_extra: dict
) -> bool:
    """MAIL_ENABLED=false 时不假装已发送：把链接写入结构化日志并返回 False（§21）。"""
    if not settings.MAIL_ENABLED:
        logger.info(log_event, extra=log_extra)
        return False
    try:
        await asyncio.to_thread(_send_smtp, to, subject, body)
        return True
    except Exception as exc:  # noqa: BLE001 - 邮件失败不回滚账号事实
        logger.warning("mail_delivery_failed", extra={"error": str(exc), "to": to})
        return False


async def _issue_token(
    db: AsyncSession, *, user_id: uuid.UUID, purpose: AuthTokenPurpose, ttl: timedelta
) -> str:
    raw = generate_token()
    db.add(
        AuthToken(
            user_id=user_id,
            purpose=str(purpose),
            token_digest=token_digest(raw),
            expires_at=_now() + ttl,
        )
    )
    return raw


async def _consume_token(db: AsyncSession, *, raw: str, purpose: AuthTokenPurpose) -> AuthToken:
    """一次性令牌：不存在/已用/已撤销/已过期都视为无效，不区分具体原因以免被枚举。"""
    stmt = select(AuthToken).where(
        AuthToken.token_digest == token_digest(raw),
        AuthToken.purpose == str(purpose),
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None or record.consumed_at is not None or record.revoked_at is not None:
        raise InvalidInput("链接无效或已被使用", code="TOKEN_INVALID")
    if _aware(record.expires_at) <= _now():
        raise InvalidInput("链接已过期，请重新申请", code="TOKEN_EXPIRED")
    record.consumed_at = _now()
    return record


# --- 跨模块接口（docs/INTERFACES.md `## identity`）-----------------------------------------


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise NotFound("用户不存在")
    return user


async def get_users(db: AsyncSession, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, User]:
    ids = list(user_ids)
    if not ids:
        return {}
    rows = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {row.id: row for row in rows}


def display_name_of(user: User) -> str:
    """匿名化后返回"已注销用户"；其余情况回退到昵称或占位名。"""
    if user.status == AccountStatus.DELETED:
        return "已注销用户"
    profile = getattr(user, "profile", None)
    name = getattr(profile, "display_name", None)
    return name or "用户"


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID, *, reason: str) -> int:
    """改密、封禁、管理员撤销都通过这里使会话失效（§14.2）。返回被撤销的会话数。"""
    result = await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=_now(), revoke_reason=reason)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def is_school_verified(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """是否持有已验证的校园身份。校验方案确定前，该范围默认拒绝（D03、§15.1）。"""
    stmt = (
        select(func.count())
        .select_from(UserIdentity)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.kind == IdentityKind.SCHOOL.value,
            UserIdentity.status == IdentityStatus.VERIFIED.value,
            UserIdentity.revoked_at.is_(None),
        )
    )
    return bool((await db.execute(stmt)).scalar_one())


# --- 注册与邮箱验证 ------------------------------------------------------------------------


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None,
    request_id: str | None,
) -> tuple[User, bool]:
    problem = password_problem(password)
    if problem:
        raise InvalidInput(problem)

    normalized = _normalize_email(email)
    # 唯一约束是最终防线，这里先查一次给出明确冲突（§29 重复注册）。
    existing = (
        await db.execute(select(User.id).where(User.email == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("该邮箱已注册", code="EMAIL_ALREADY_REGISTERED")

    name = (display_name or "").strip()[:80] or normalized.split("@")[0]
    user = User(
        email=normalized,
        password_hash=hash_password(password),
        role=Role.USER.value,
        status=AccountStatus.PENDING_VERIFICATION.value,
    )
    db.add(user)
    await db.flush()

    db.add(UserProfile(user_id=user.id, display_name=name))
    db.add(
        UserIdentity(
            user_id=user.id,
            kind=IdentityKind.EMAIL.value,
            identifier=normalized,
            status=IdentityStatus.PENDING.value,
        )
    )

    raw = await _issue_token(
        db,
        user_id=user.id,
        purpose=AuthTokenPurpose.EMAIL_VERIFICATION,
        ttl=timedelta(hours=EMAIL_VERIFICATION_TTL_HOURS),
    )
    link = _verification_link("/verify-email", raw)
    sent = await _deliver_mail(
        to=normalized,
        subject="验证你的邮箱",
        body=(
            f"请在 {EMAIL_VERIFICATION_TTL_HOURS} 小时内打开以下链接完成邮箱验证：\n{link}\n"
            "验证邮箱只表示你当时控制该邮箱，不代表完成官方学生身份认证。"
        ),
        log_event="email_verification_token",
        log_extra={"user_id": str(user.id), "email": normalized, "verification_url": link},
    )

    await record_audit(
        db,
        action="identity.register",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={"status": user.status, "role": user.role, "email_verified": False},
        request_id=request_id,
    )
    return user, sent


async def request_email_verification(
    db: AsyncSession, *, email: str, request_id: str | None
) -> None:
    """无论邮箱是否存在都返回同一结果，不暴露账号是否存在（§14.2）。"""
    normalized = _normalize_email(email)
    user = (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
    if (
        user is None
        or user.email_verified_at is not None
        or user.status in (AccountStatus.BANNED, AccountStatus.DELETED)
    ):
        return

    raw = await _issue_token(
        db,
        user_id=user.id,
        purpose=AuthTokenPurpose.EMAIL_VERIFICATION,
        ttl=timedelta(hours=EMAIL_VERIFICATION_TTL_HOURS),
    )
    link = _verification_link("/verify-email", raw)
    await _deliver_mail(
        to=normalized,
        subject="验证你的邮箱",
        body=f"请在 {EMAIL_VERIFICATION_TTL_HOURS} 小时内打开以下链接完成邮箱验证：\n{link}",
        log_event="email_verification_token",
        log_extra={"user_id": str(user.id), "email": normalized, "verification_url": link},
    )
    await record_audit(
        db,
        action="identity.email_verification_requested",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        request_id=request_id,
    )


async def confirm_email_verification(
    db: AsyncSession, *, token: str, request_id: str | None
) -> User:
    record = await _consume_token(db, raw=token, purpose=AuthTokenPurpose.EMAIL_VERIFICATION)
    user = await get_user(db, record.user_id)
    now = _now()

    if user.email_verified_at is None:
        user.email_verified_at = now
    # 只把待验证账号转为可用；不覆盖暂停/封禁等既有状态。
    if user.status == AccountStatus.PENDING_VERIFICATION:
        user.status = AccountStatus.ACTIVE.value

    identity = (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id,
                UserIdentity.kind == IdentityKind.EMAIL.value,
                UserIdentity.identifier == user.email,
            )
        )
    ).scalar_one_or_none()
    if identity is not None:
        identity.status = IdentityStatus.VERIFIED.value
        identity.verified_at = now
        identity.verified_source = "email_link"

    await record_audit(
        db,
        action="identity.email_verified",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={"status": user.status, "email_verified_at": now.isoformat()},
        request_id=request_id,
    )
    return user


# --- 登录、登出与会话 ----------------------------------------------------------------------


async def _note_login_failure(
    db: AsyncSession, *, email: str, user_id: uuid.UUID | None, request_id: str | None
) -> None:
    allowed = await ratelimit.check_rate_limit(
        f"login:fail:{email}",
        limit=settings.LOGIN_FAILURE_LOCKOUT_THRESHOLD,
        window_seconds=LOGIN_FAILURE_WINDOW_SECONDS,
    )
    await record_audit(
        db,
        action="identity.login_failed",
        object_type="user",
        object_id=user_id,
        actor_id=user_id,
        reason="bad_credentials",
        request_id=request_id,
    )
    if not allowed:
        # 达阈值只延迟，不永久锁定；窗口滑动后自动恢复（§14.2、§28）。
        await asyncio.sleep(LOGIN_FAILURE_DELAY_SECONDS)


async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    client_ip: str | None,
    request_id: str | None,
) -> tuple[User, str, datetime]:
    normalized = _normalize_email(email)

    # 综合账号与来源限流，避免用恶意失败永久锁死他人账号（§28）。
    if not await ratelimit.check_rate_limit(
        f"login:account:{normalized}",
        limit=settings.LOGIN_RATE_LIMIT_PER_HOUR,
        window_seconds=3600,
    ):
        raise RateLimited("登录尝试过于频繁，请稍后再试")
    if client_ip and not await ratelimit.check_rate_limit(
        f"login:ip:{client_ip}",
        limit=settings.LOGIN_RATE_LIMIT_PER_HOUR * LOGIN_IP_LIMIT_MULTIPLIER,
        window_seconds=3600,
    ):
        raise RateLimited("登录尝试过于频繁，请稍后再试")

    user = (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        await _note_login_failure(
            db, email=normalized, user_id=user.id if user else None, request_id=request_id
        )
        raise Unauthenticated("邮箱或密码不正确")

    # 封禁不能登录；暂停仍可登录查看申诉入口（§14.1）。
    if user.status in (AccountStatus.BANNED, AccountStatus.DELETED):
        raise Forbidden("账号当前不可用", details={"status": user.status})

    await ratelimit.reset_rate_limit(f"login:fail:{normalized}")

    raw = generate_token()
    now = _now()
    user_agent_value = (user_agent or "").strip()[:400] or None
    session = Session(
        user_id=user.id,
        token_digest=token_digest(raw),
        issued_at=now,
        expires_at=now + timedelta(hours=settings.SESSION_TTL_HOURS),
        last_seen_at=now,
        user_agent=user_agent_value,
        client_ip=client_ip,
    )
    db.add(session)
    await db.flush()

    await record_audit(
        db,
        action="identity.login",
        object_type="session",
        object_id=session.id,
        actor_id=user.id,
        after={"user_agent": user_agent_value, "client_ip": client_ip},
        request_id=request_id,
    )
    return user, raw, session.expires_at


async def logout(db: AsyncSession, *, session: Session, request_id: str | None) -> None:
    if session.revoked_at is None:
        session.revoked_at = _now()
        session.revoke_reason = "logout"
    await record_audit(
        db,
        action="identity.logout",
        object_type="session",
        object_id=session.id,
        actor_id=session.user_id,
        request_id=request_id,
    )


async def touch_session(db: AsyncSession, session: Session) -> None:
    """按 SESSION_TOUCH_INTERVAL_MINUTES 节流更新 last_seen_at，避免每次请求都写库。"""
    now = _now()
    last = session.last_seen_at
    if last is None or _aware(last) + timedelta(
        minutes=settings.SESSION_TOUCH_INTERVAL_MINUTES
    ) <= now:
        session.last_seen_at = now


async def list_sessions(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int | None,
    cursor: str | None,
    current_session_id: uuid.UUID | None,
) -> CursorPage[SessionItem]:
    size = normalize_page_size(limit)
    stmt = select(Session).where(Session.user_id == user_id)
    if cursor:
        data = decode_cursor(cursor)
        try:
            created_at = datetime.fromisoformat(str(data["created_at"]))
            last_id = uuid.UUID(str(data["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput("cursor 无效") from exc
        stmt = stmt.where(
            or_(
                Session.created_at < created_at,
                and_(Session.created_at == created_at, Session.id < last_id),
            )
        )

    rows = list(
        (
            await db.execute(
                stmt.order_by(Session.created_at.desc(), Session.id.desc()).limit(size + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > size
    rows = rows[:size]
    items = [
        SessionItem(
            id=row.id,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            last_seen_at=row.last_seen_at,
            revoked_at=row.revoked_at,
            revoke_reason=row.revoke_reason,
            user_agent=row.user_agent,
            client_ip=row.client_ip,
            current=row.id == current_session_id,
        )
        for row in rows
    ]
    next_cursor = None
    if has_more and rows:
        next_cursor = encode_cursor(
            {"created_at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)}
        )
    return CursorPage[SessionItem](items=items, next_cursor=next_cursor, has_more=has_more)


async def revoke_session(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    request_id: str | None,
) -> None:
    """只能撤销本人会话；他人或不存在统一返回 NotFound，避免泄露存在性（§25.1）。"""
    session = (
        await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise NotFound("会话不存在")
    if session.revoked_at is None:
        session.revoked_at = _now()
        session.revoke_reason = "user_revoked"
    await record_audit(
        db,
        action="identity.session_revoked",
        object_type="session",
        object_id=session.id,
        actor_id=user_id,
        request_id=request_id,
    )


# --- 找回密码 ------------------------------------------------------------------------------


async def request_password_reset(
    db: AsyncSession, *, email: str, request_id: str | None
) -> None:
    """无论邮箱是否存在都返回同一结果（§14.2）。"""
    normalized = _normalize_email(email)
    user = (await db.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
    if user is None or user.status in (AccountStatus.BANNED, AccountStatus.DELETED):
        return

    raw = await _issue_token(
        db,
        user_id=user.id,
        purpose=AuthTokenPurpose.PASSWORD_RESET,
        ttl=timedelta(minutes=PASSWORD_RESET_TTL_MINUTES),
    )
    link = _verification_link("/reset-password", raw)
    await _deliver_mail(
        to=normalized,
        subject="重置密码",
        body=f"请在 {PASSWORD_RESET_TTL_MINUTES} 分钟内打开以下链接重置密码：\n{link}",
        log_event="password_reset_token",
        log_extra={"user_id": str(user.id), "email": normalized, "reset_url": link},
    )
    await record_audit(
        db,
        action="identity.password_reset_requested",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        request_id=request_id,
    )


async def confirm_password_reset(
    db: AsyncSession, *, token: str, new_password: str, request_id: str | None
) -> User:
    problem = password_problem(new_password)
    if problem:
        raise InvalidInput(problem)

    record = await _consume_token(db, raw=token, purpose=AuthTokenPurpose.PASSWORD_RESET)
    user = await get_user(db, record.user_id)
    user.password_hash = hash_password(new_password)
    revoked = await revoke_all_sessions(db, user.id, reason="password_reset")

    await record_audit(
        db,
        action="identity.password_reset",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={"revoked_sessions": revoked},
        request_id=request_id,
    )
    return user


# --- 当前账号 ------------------------------------------------------------------------------


async def me_summary(db: AsyncSession, *, user: User) -> MeResponse:
    profile = getattr(user, "profile", None)
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        display_name=display_name_of(user),
        avatar_url=profile.avatar_url if profile else None,
        email_verified=user.email_verified_at is not None,
        school_verified=await is_school_verified(db, user.id),
        email_verified_at=user.email_verified_at,
        college_id=profile.college_id if profile else None,
        major_id=profile.major_id if profile else None,
        enrollment_year=profile.enrollment_year if profile else None,
        preferences=profile.preferences if profile else {},
        created_at=user.created_at,
    )


async def update_me(
    db: AsyncSession, *, user: User, payload: dict, request_id: str | None
) -> MeResponse:
    profile = getattr(user, "profile", None)
    if profile is None:
        profile = UserProfile(user_id=user.id, display_name=display_name_of(user))
        db.add(profile)
        await db.flush()

    if "display_name" in payload:
        name = (payload["display_name"] or "").strip()
        if not name:
            raise InvalidInput("昵称不能为空")
        profile.display_name = name[:80]
    for field in ("avatar_url", "college_id", "major_id", "enrollment_year"):
        if field in payload:
            setattr(profile, field, payload[field])
    if "preferences" in payload and payload["preferences"] is not None:
        profile.preferences = payload["preferences"]

    await record_audit(
        db,
        action="identity.profile_updated",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        # UUID 等值转字符串，保证审计 JSON 可序列化。
        after={key: str(value) for key, value in payload.items()},
        request_id=request_id,
    )
    return await me_summary(db, user=user)
