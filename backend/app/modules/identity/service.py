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
    NotEnabled,
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
from app.db import async_session_factory
from app.modules.identity.models import (
    AuthToken,
    EmailChangeRequest,
    Session,
    User,
    UserIdentity,
    UserProfile,
)
from app.modules.identity.schemas import ActionMessage, MeResponse, SessionItem

logger = logging.getLogger("app.identity")


async def set_account_status(db: AsyncSession, *, user_id: uuid.UUID, status: AccountStatus,
    reason: str | None, actor_id: uuid.UUID, request_id: str) -> str:
    """人工暂停/恢复的唯一写入口；调用方负责同事务审计和会话撤销。"""
    actor = await db.get(User, actor_id)
    if actor is None or actor.role != Role.ADMIN or actor.status != AccountStatus.ACTIVE:
        raise Forbidden("需要有效管理员账号")
    target = await db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if target is None:
        raise NotFound("账号不存在")
    if target.id == actor_id or target.role == Role.ADMIN:
        raise Forbidden("不能在此暂停或恢复管理员账号")
    if not reason or not reason.strip():
        raise InvalidInput("请填写处理理由")
    expected = AccountStatus.ACTIVE if status == AccountStatus.SUSPENDED else AccountStatus.SUSPENDED
    if status not in (AccountStatus.ACTIVE, AccountStatus.SUSPENDED) or target.status != expected:
        raise Conflict("账号状态已变化，或不支持此状态流转", code="ACCOUNT_STATE_CHANGED")
    before = target.status
    target.status = status.value
    await db.flush()
    return before

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


async def revoke_all_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, reason: str,
    keep_session_id: uuid.UUID | None = None,
) -> int:
    """改密、封禁、管理员撤销都通过这里使会话失效（§14.2）。返回被撤销的会话数。

    keep_session_id 供「保留当前会话、撤销其余」的场景（beta14 改密与一键退出其他设备）。
    """
    stmt = (
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=_now(), revoke_reason=reason)
    )
    if keep_session_id is not None:
        stmt = stmt.where(Session.id != keep_session_id)
    result = await db.execute(stmt.execution_options(synchronize_session=False))
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
    # 登录失败会使请求事务回滚，审计需独立提交，不能提交调用方业务事务。
    async with async_session_factory() as audit_db:
        await record_audit(
            audit_db,
            action="identity.login_failed",
            object_type="user",
            object_id=user_id,
            actor_id=user_id,
            reason="bad_credentials",
            request_id=request_id,
        )
        await audit_db.commit()
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
    otp: str | None = None,
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

    # 同一账号登录串行创建和淘汰会话，防止并发请求各自观察到未超限。
    verified_hash = user.password_hash
    user = (await db.execute(
        select(User).where(User.id == user.id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
    if user.password_hash != verified_hash and not verify_password(password, user.password_hash):
        # 等锁期间密码可能已修改；不在持有用户锁时开启审计写事务。
        raise Unauthenticated("邮箱或密码不正确")

    # 封禁不能登录；暂停仍可登录查看申诉入口（§14.1）。
    if user.status in (AccountStatus.BANNED, AccountStatus.DELETED):
        raise Forbidden("账号当前不可用", details={"status": user.status})

    from app.modules.identity.mfa import verify as verify_mfa
    mfa_verified = await verify_mfa(db, user.id, otp)

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
        mfa_verified_at=now if mfa_verified else None,
    )
    db.add(session)
    await db.flush()

    # 并发会话上限（beta14）：超限时按 issued_at 升序淘汰最旧会话，仅记审计、不阻断本次登录。
    # 新会话 issued_at 最新，天然排在末尾不被淘汰。
    max_active = settings.SESSION_MAX_ACTIVE_PER_USER
    if max_active > 0:
        active = list(
            (
                await db.execute(
                    select(Session)
                    .where(
                        Session.user_id == user.id,
                        Session.revoked_at.is_(None),
                        Session.expires_at > now,
                    )
                    .order_by(Session.issued_at.asc(), Session.id.asc())
                )
            )
            .scalars()
            .all()
        )
        for stale in active[: max(len(active) - max_active, 0)]:
            stale.revoked_at = now
            stale.revoke_reason = "session_limit"
            await record_audit(
                db,
                action="identity.session_limit_evicted",
                object_type="session",
                object_id=stale.id,
                actor_id=user.id,
                reason="超过并发会话上限",
                after={"max_active": max_active},
                request_id=request_id,
            )

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


# --- 登录态密码修改与一键退出其他设备（beta14）------------------------------------------


async def change_password(
    db: AsyncSession,
    *,
    user: User,
    current_session_id: uuid.UUID,
    current_password: str,
    new_password: str,
    request_id: str | None,
) -> ActionMessage:
    """登录态修改密码：校验旧密码 → 强度校验 → 更新哈希 → 保留当前会话、撤销其余（§14.2）。

    旧密码错误统一按 InvalidInput 拒绝，不区分「用户不存在」与「密码错误」；
    审计只记录撤销数量，不记录任何密码。
    """
    if not verify_password(current_password, user.password_hash):
        raise InvalidInput("当前密码不正确", code="PASSWORD_INCORRECT")
    # 复用注册/重置时同一套强度校验，避免多处密码规则漂移。
    problem = password_problem(new_password)
    if problem:
        raise InvalidInput(problem)

    user.password_hash = hash_password(new_password)
    revoked = await revoke_all_sessions(
        db, user.id, reason="password_change", keep_session_id=current_session_id
    )

    await record_audit(
        db,
        action="identity.password_changed",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={"revoked_other_sessions": revoked},
        request_id=request_id,
    )
    return ActionMessage(message="密码已修改，其他设备上的会话已退出，当前会话保持不变")


async def revoke_other_sessions(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_session_id: uuid.UUID,
    request_id: str | None,
) -> int:
    """一键退出其他设备：撤销除当前会话外的全部会话（§14.2）。返回被撤销的会话数。"""
    revoked = await revoke_all_sessions(
        db, user_id, reason="user_revoked_others", keep_session_id=current_session_id
    )
    await record_audit(
        db,
        action="identity.other_sessions_revoked",
        object_type="user",
        object_id=user_id,
        actor_id=user_id,
        after={"revoked_sessions": revoked},
        request_id=request_id,
    )
    return revoked


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
    from app.modules.identity.models import UserMFA
    mfa_record = await db.get(UserMFA, user.id)
    if mfa_record is not None:
        # 邮箱找回不能移除验证器；旧恢复码失效，仍需原验证器才能登录。
        mfa_record.recovery_hashes = []
        mfa_record.revision += 1
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
    from app.core.permissions import effective_permissions
    profile = getattr(user, "profile", None)
    return MeResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        permissions=sorted(await effective_permissions(db, user)) if user.status == AccountStatus.ACTIVE else [],
        status=user.status,
        display_name=display_name_of(user),
        avatar_url=profile.avatar_url if profile else None,
        gender=profile.gender if profile else None,
        bio=profile.bio if profile else None,
        email_verified=user.email_verified_at is not None,
        school_verified=await is_school_verified(db, user.id),
        email_verified_at=user.email_verified_at,
        college_id=profile.college_id if profile else None,
        major_id=profile.major_id if profile else None,
        enrollment_year=profile.enrollment_year if profile else None,
        preferences=profile.preferences if profile else {},
        created_at=user.created_at,
    )


async def _ensure_profile(db: AsyncSession, user: User) -> UserProfile:
    profile = getattr(user, "profile", None)
    if profile is None:
        profile = UserProfile(user_id=user.id, display_name=display_name_of(user))
        db.add(profile)
        await db.flush()
        user.profile = profile
    return profile


async def update_me(
    db: AsyncSession, *, user: User, payload: dict, request_id: str | None
) -> MeResponse:
    profile = await _ensure_profile(db, user)

    if "display_name" in payload:
        name = (payload["display_name"] or "").strip()
        if not name:
            raise InvalidInput("昵称不能为空")
        profile.display_name = name[:80]
    for field in ("avatar_url", "gender", "bio", "college_id", "major_id", "enrollment_year"):
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
        after={"changed_fields": sorted(payload)},
        request_id=request_id,
    )
    return await me_summary(db, user=user)


# 声明类型 -> (Pillow 格式名, 存储后缀)。头像直传公开桶，不进入隔离区扫描（§14.2、§43）。
# MIME 归一：Windows 的文件选择器/浏览器常把 JPEG 报成 image/jpg 或 image/pjpeg，
# 旧工具也会报 image/x-png，这里按别名收敛后再与真实字节比对，避免误拒正常头像。
_AVATAR_FORMATS = {
    "image/png": ("PNG", ".png"),
    "image/x-png": ("PNG", ".png"),
    "image/jpeg": ("JPEG", ".jpg"),
    "image/jpg": ("JPEG", ".jpg"),
    "image/pjpeg": ("JPEG", ".jpg"),
    "image/webp": ("WEBP", ".webp"),
    "image/gif": ("GIF", ".gif"),
}
_AVATAR_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}


async def update_avatar(
    db: AsyncSession, *, user: User, data: bytes, content_type: str, request_id: str | None
) -> MeResponse:
    """校验并直接上传头像到公开桶，返回新地址；与资料字段编辑相互独立。"""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    from app.core.storage import build_storage_key, storage

    mime = (content_type or "").split(";")[0].strip().lower()
    mime = _AVATAR_MIME_ALIASES.get(mime, mime)
    spec = _AVATAR_FORMATS.get(mime)
    if spec is None:
        raise InvalidInput("头像仅支持 PNG、JPEG、WebP 或 GIF")
    if not data:
        raise InvalidInput("头像文件为空")
    if len(data) > settings.AVATAR_MAX_SIZE_BYTES:
        limit_mib = settings.AVATAR_MAX_SIZE_BYTES // (1024 * 1024)
        raise InvalidInput(f"头像不能超过 {limit_mib} MiB")
    # 上传声明的类型不可信：按真实字节确认是可解码图片，避免把任意内容放进公开桶。
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
            detected = (image.format or "").upper()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidInput("头像不是有效的图片") from exc
    if detected != spec[0]:
        raise InvalidInput("头像内容与文件类型不一致")

    profile = await _ensure_profile(db, user)
    key = build_storage_key(f"avatars/{user.id}", f"avatar{spec[1]}")
    await storage.put_public_object(key, data, mime)
    previous = profile.avatar_url
    profile.avatar_url = storage.public_url(key)
    await record_audit(
        db,
        action="identity.avatar_updated",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        before={"avatar_url": previous},
        after={"avatar_url": profile.avatar_url},
        request_id=request_id,
    )
    return await me_summary(db, user=user)


# --- 账号注销与个人数据导出（§9「注销账号」）-------------------------------------------------

# 固定确认词：防止误触的显式二次确认，匹配失败按业务错误拒绝。
DELETION_CONFIRM_PHRASE = "注销账号"
# 注销立即生效（不可登录、公开资料匿名化）；邮箱与审计/业务事实按本保留期继续保留，
# 供申诉与对账，期满由运维清理。本轮不实现自动清理定时任务，只落地字段与说明（§9）。
ACCOUNT_RETENTION_DAYS = 30


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def delete_account(
    db: AsyncSession,
    *,
    user: User,
    password: str,
    confirm: str,
    request_id: str | None,
) -> ActionMessage:
    """注销账号：校验密码与确认词，匿名化个人资料并撤销全部会话。

    业务事实（上传资源、评论、评分、审计日志、事件记录）一律保留，评论不匿名化，
    仅靠 `display_name_of` 在对外展示时自然显示为「已注销用户」（§9）。
    """
    if user.status == AccountStatus.DELETED:
        raise Conflict("账号已注销", code="ACCOUNT_ALREADY_DELETED")
    if confirm.strip() != DELETION_CONFIRM_PHRASE:
        raise InvalidInput(
            f"请输入确认词「{DELETION_CONFIRM_PHRASE}」以确认注销",
            code="CONFIRMATION_REQUIRED",
        )
    # 复用项目既有密码校验实现，不另发明哈希逻辑；审计不记录密码。
    if not verify_password(password, user.password_hash):
        await record_audit(
            db,
            action="identity.account_deletion_failed",
            object_type="user",
            object_id=user.id,
            actor_id=user.id,
            reason="password_mismatch",
            request_id=request_id,
        )
        raise Forbidden("密码不正确", code="PASSWORD_INCORRECT")

    now = _now()
    user.status = AccountStatus.DELETED.value
    user.deleted_at = now
    from app.modules.identity.models import UserMFA
    mfa_record = await db.get(UserMFA, user.id)
    if mfa_record is not None:
        mfa_record.secret_ciphertext = None
        mfa_record.enabled_at = None
        mfa_record.recovery_hashes = []
        mfa_record.revision += 1

    # 匿名化个人资料；邮箱保留在保留期内，故不改动 users.email。
    profile = getattr(user, "profile", None)
    if profile is not None:
        profile.display_name = "已注销用户"
        profile.avatar_url = None
        profile.gender = None
        profile.bio = None
        profile.college_id = None
        profile.major_id = None
        profile.enrollment_year = None
        profile.preferences = {}

    revoked = await revoke_all_sessions(db, user.id, reason="account_deleted")

    await record_audit(
        db,
        action="identity.account_deleted",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={
            "status": user.status,
            "deleted_at": now.isoformat(),
            "revoked_sessions": revoked,
            "retention_days": ACCOUNT_RETENTION_DAYS,
        },
        request_id=request_id,
    )
    return ActionMessage(
        message=(
            "账号已注销：登录会话已全部撤销，公开资料已匿名化。"
            f"邮箱与审计/业务事实将保留 {ACCOUNT_RETENTION_DAYS} 天，供申诉与对账。"
        )
    )


async def run_retention_cleanup(
    db: AsyncSession, *, retention_days: int | None = None, now: datetime | None = None
) -> int:
    """保留期清理：注销超过保留期且尚未清理的账号，抹除可识别 PII（§9）。

    只处理 status=DELETED、deleted_at 已超保留期、purged_at 为空的账号；
    邮箱置为带 UUID 的唯一墓碑值（满足唯一约束、不可登录），清空邮箱验证时间，
    撤销残留会话，打上 purged_at 时间戳。不删除用户行与任何业务/审计/内容记录。

    返回本次清理的账号数。``retention_days``/``now`` 可注入，便于演练与测试。
    """
    days = ACCOUNT_RETENTION_DAYS if retention_days is None else retention_days
    moment = _now() if now is None else now
    cutoff = moment - timedelta(days=days)

    candidates = list(
        (
            await db.execute(
                select(User).where(
                    User.status == AccountStatus.DELETED.value,
                    User.deleted_at.is_not(None),
                    User.purged_at.is_(None),
                    User.deleted_at <= cutoff,
                )
            )
        ).scalars()
        .all()
    )

    purged = 0
    for user in candidates:
        # 邮箱是唯一约束列：用含 UUID 的墓碑值保证全局唯一且不可作为登录凭据。
        user.email = f"purged+{user.id}@purged.invalid"
        user.email_verified_at = None
        user.purged_at = moment
        # 兜底撤销保留期内可能恢复的会话；注销时已撤销，这里确保彻底失效。
        await revoke_all_sessions(db, user.id, reason="retention_purged")
        # 关联邮箱身份一并撤销，避免旧邮箱仍挂在身份声明上（不删记录）。
        await db.execute(
            update(UserIdentity)
            .where(
                UserIdentity.user_id == user.id,
                UserIdentity.kind == IdentityKind.EMAIL.value,
                UserIdentity.revoked_at.is_(None),
            )
            .values(revoked_at=moment)
        )
        await record_audit(
            db,
            action="identity.account_purged",
            object_type="user",
            object_id=user.id,
            after={
                "purged_at": moment.isoformat(),
                "retention_days": days,
            },
            request_id="retention-cleanup",
        )
        purged += 1

    await db.flush()
    return purged


async def export_my_data(db: AsyncSession, *, user: User) -> dict:
    """导出本人数据的 JSON 结构（§9）。

    只含本人数据：不含文件字节、不含签名 URL、不含他人评论或他人会话。
    注销账号不可导出（对外由 get_current_user 拦截，这里再兜底一次）。
    """
    if user.status == AccountStatus.DELETED:
        raise Forbidden("账号已注销，无法导出数据")

    # 跨模块模型只在函数体内局部导入，避免循环依赖（docs/ARCHITECTURE.md §2）。
    from app.modules.content.models import Resource, ResourceVersion
    from app.modules.interaction.models import (
        Comment,
        Favorite,
        Follow,
        Rating,
        ResourceRequest,
        ResourceRequestResponse,
    )
    from app.modules.interaction.service import comment_status
    from app.modules.notification.models import Notification

    profile = getattr(user, "profile", None)

    async def _all(stmt) -> list:
        return list((await db.execute(stmt)).scalars().all())

    follow_rows = await _all(
        select(Follow).where(Follow.user_id == user.id).order_by(Follow.created_at)
    )
    favorite_rows = await _all(
        select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.created_at)
    )
    rating_rows = await _all(
        select(Rating).where(Rating.user_id == user.id).order_by(Rating.created_at)
    )
    comment_rows = await _all(
        select(Comment).where(Comment.user_id == user.id).order_by(Comment.created_at)
    )
    notification_rows = await _all(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at)
    )
    request_rows = await _all(
        select(ResourceRequest)
        .where(ResourceRequest.requester_id == user.id)
        .order_by(ResourceRequest.created_at)
    )
    response_rows = await _all(
        select(ResourceRequestResponse)
        .where(ResourceRequestResponse.submitted_by == user.id)
        .order_by(ResourceRequestResponse.created_at)
    )
    session_rows = await _all(
        select(Session).where(Session.user_id == user.id).order_by(Session.created_at)
    )

    resource_rows = await _all(
        select(Resource).where(Resource.owner_id == user.id).order_by(Resource.created_at)
    )
    latest_version: dict[uuid.UUID, ResourceVersion] = {}
    if resource_rows:
        version_rows = await _all(
            select(ResourceVersion)
            .where(ResourceVersion.resource_id.in_([row.id for row in resource_rows]))
            .order_by(ResourceVersion.version_no.desc())
        )
        for version in version_rows:
            latest_version.setdefault(version.resource_id, version)

    return {
        "exported_at": _now().isoformat(),
        "retention": {
            "policy": "deleted_account",
            "retention_days": ACCOUNT_RETENTION_DAYS,
            "note": "注销立即生效；邮箱与审计/业务事实按保留期保留，供申诉与对账，期满由运维清理",
        },
        "account": {
            "id": str(user.id),
            "email": user.email,
            "role": user.role,
            "status": user.status,
            "display_name": display_name_of(user),
            "email_verified": user.email_verified_at is not None,
            "email_verified_at": _iso(user.email_verified_at),
            "deleted_at": _iso(user.deleted_at),
            "created_at": _iso(user.created_at),
        },
        "profile": {
            "display_name": profile.display_name if profile else None,
            "avatar_url": profile.avatar_url if profile else None,
            "gender": profile.gender if profile else None,
            "bio": profile.bio if profile else None,
            "college_id": str(profile.college_id) if profile and profile.college_id else None,
            "major_id": str(profile.major_id) if profile and profile.major_id else None,
            "enrollment_year": profile.enrollment_year if profile else None,
            "preferences": profile.preferences if profile else {},
        },
        "follows": [
            {
                "target_type": row.target_type,
                "target_id": str(row.target_id),
                "created_at": _iso(row.created_at),
            }
            for row in follow_rows
        ],
        "favorites": [
            {"resource_id": str(row.resource_id), "created_at": _iso(row.created_at)}
            for row in favorite_rows
        ],
        "ratings": [
            {
                "resource_id": str(row.resource_id),
                "version_id": str(row.version_id),
                "score": row.score,
                "created_at": _iso(row.created_at),
            }
            for row in rating_rows
        ],
        "comments": [
            {
                "id": str(row.id),
                "resource_id": str(row.resource_id),
                "parent_id": str(row.parent_id) if row.parent_id else None,
                "body": row.body,
                "status": comment_status(row),
                "withdrawn_at": _iso(row.withdrawn_at),
                "is_hidden": row.is_hidden,
                "created_at": _iso(row.created_at),
            }
            for row in comment_rows
        ],
        "resources": [
            {
                "id": str(row.id),
                "course_id": str(row.course_id),
                "resource_type": row.resource_type,
                "publication_status": row.publication_status,
                "title": latest_version[row.id].title if row.id in latest_version else None,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in resource_rows
        ],
        "notifications": [
            {
                "id": str(row.id),
                "type": row.type,
                "title": row.title,
                "body": row.body,
                "data": row.data,
                "is_read": row.is_read,
                "read_at": _iso(row.read_at),
                "created_at": _iso(row.created_at),
            }
            for row in notification_rows
        ],
        "requests": [
            {
                "id": str(row.id),
                "course_id": str(row.course_id),
                "scope": row.scope,
                "description": row.description,
                "status": row.status,
                "resolved_resource_id": (
                    str(row.resolved_resource_id) if row.resolved_resource_id else None
                ),
                "created_at": _iso(row.created_at),
                "handled_at": _iso(row.handled_at),
            }
            for row in request_rows
        ],
        "request_responses": [
            {
                "id": str(row.id),
                "request_id": str(row.request_id),
                "resource_id": str(row.resource_id),
                "created_at": _iso(row.created_at),
            }
            for row in response_rows
        ],
        "sessions": [
            {
                "id": str(row.id),
                "issued_at": _iso(row.issued_at),
                "expires_at": _iso(row.expires_at),
                "last_seen_at": _iso(row.last_seen_at),
                "revoked_at": _iso(row.revoked_at),
                "revoke_reason": row.revoke_reason,
                "user_agent": row.user_agent,
                "client_ip": row.client_ip,
                "created_at": _iso(row.created_at),
            }
            for row in session_rows
        ],
    }


# --- 邮箱换绑（0.0.3）------------------------------------------------------- #
EMAIL_CHANGE_TTL_HOURS = 2
EMAIL_CHANGE_COOLDOWN_HOURS = 24


async def request_email_change(
    db: AsyncSession,
    *,
    user: User,
    session: Session,
    new_email: str,
    password: str,
    request_id: str | None,
) -> ActionMessage:
    """发起换绑：校验密码与新邮箱，向新邮箱发送确认令牌，同时告知旧邮箱。

    邮件服务关闭时明确返回 NotEnabled，不假装「已发送」。同一账号 24 小时内只能发起一次，
    防止会话被盗后反复试探；新邮箱在确认前不写入用户表。
    """
    if not settings.MAIL_ENABLED:
        raise NotEnabled("邮件服务未启用，暂时不能换绑邮箱")

    if not verify_password(password, user.password_hash):
        raise InvalidInput("密码不正确")

    normalized = _normalize_email(new_email)
    if not normalized or "@" not in normalized:
        raise InvalidInput("邮箱格式不正确")
    if normalized == user.email:
        raise InvalidInput("新邮箱与当前邮箱相同")

    taken = (
        await db.execute(select(User.id).where(User.email == normalized))
    ).scalar_one_or_none()
    if taken is not None:
        raise Conflict("该邮箱已被其他账号使用")

    now = _now()
    recent = (
        await db.execute(
            select(EmailChangeRequest.id).where(
                EmailChangeRequest.user_id == user.id,
                EmailChangeRequest.created_at >= now - timedelta(hours=EMAIL_CHANGE_COOLDOWN_HOURS),
            )
        )
    ).first()
    if recent is not None:
        raise RateLimited(
            "换绑申请过于频繁，请稍后再试",
            details={"retry_after": EMAIL_CHANGE_COOLDOWN_HOURS * 3600},
        )

    # 生产环境且已启用 MFA：换绑属于敏感操作，要求近期验证（与权限体系一致）。
    if settings.is_prod:
        from app.modules.identity.models import UserMFA
        from app.modules.identity.mfa import require_recent

        mfa = await db.get(UserMFA, user.id)
        if mfa is not None and mfa.enabled_at is not None:
            await require_recent(db, user, session)

    # 作废此前未使用的换绑令牌，只保留最新一条。
    await db.execute(
        update(EmailChangeRequest)
        .where(
            EmailChangeRequest.user_id == user.id,
            EmailChangeRequest.consumed_at.is_(None),
            EmailChangeRequest.cancelled_at.is_(None),
        )
        .values(cancelled_at=now)
        .execution_options(synchronize_session=False)
    )

    raw = generate_token()
    db.add(
        EmailChangeRequest(
            user_id=user.id,
            new_email=normalized,
            token_digest=token_digest(raw),
            expires_at=now + timedelta(hours=EMAIL_CHANGE_TTL_HOURS),
        )
    )
    link = _verification_link("/settings/email/confirm", raw)
    await _deliver_mail(
        to=normalized,
        subject="确认更换邮箱",
        body=f"确认将账号邮箱更换为 {normalized}：{link}\n链接 {EMAIL_CHANGE_TTL_HOURS} 小时内有效。",
        log_event="email_change_link_issued",
        log_extra={"user_id": str(user.id), "request_id": request_id},
    )
    # 旧邮箱安全告知：让原持有人知道有人发起了换绑（不包含令牌）。
    await _deliver_mail(
        to=user.email,
        subject="邮箱换绑申请提醒",
        body=(
            f"你的账号发起了更换邮箱的申请，目标邮箱为 {normalized}。\n"
            "如果这不是你的操作，请立即修改密码并联系管理员。"
        ),
        log_event="email_change_notice_sent",
        log_extra={"user_id": str(user.id), "request_id": request_id},
    )
    await record_audit(
        db,
        action="identity.email_change_requested",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        after={"new_email_domain": normalized.split("@", 1)[-1]},
        reason="发起邮箱换绑",
        request_id=request_id,
    )
    await db.flush()
    return ActionMessage(message="确认邮件已发送到新邮箱，请在有效期内完成确认")


async def confirm_email_change(
    db: AsyncSession, *, raw_token: str, request_id: str | None
) -> ActionMessage:
    """确认换绑：令牌一次性、有期限；成功后撤销全部会话，要求重新登录。"""
    row = (
        await db.execute(
            select(EmailChangeRequest).where(
                EmailChangeRequest.token_digest == token_digest(raw_token)
            )
        )
    ).scalar_one_or_none()
    if row is None or row.consumed_at is not None or row.cancelled_at is not None:
        raise InvalidInput("链接无效或已被使用", code="TOKEN_INVALID")
    if _aware(row.expires_at) <= _now():
        raise InvalidInput("链接已过期，请重新申请", code="TOKEN_EXPIRED")

    user = await db.get(User, row.user_id)
    if user is None:
        raise InvalidInput("链接无效或已被使用", code="TOKEN_INVALID")

    taken = (
        await db.execute(select(User.id).where(User.email == row.new_email, User.id != user.id))
    ).scalar_one_or_none()
    if taken is not None:
        # 并发场景：申请后该邮箱被注册，确认失败并作废本条申请。
        row.cancelled_at = _now()
        raise Conflict("该邮箱已被其他账号使用，请重新申请")

    previous_email = user.email
    user.email = row.new_email
    # 已通过链接确认持有该邮箱，视为已验证（与注册验证同源语义 §14.1）。
    user.email_verified_at = _now()
    row.consumed_at = _now()
    await revoke_all_sessions(db, user.id, reason="邮箱换绑后要求重新登录")
    await record_audit(
        db,
        action="identity.email_changed",
        object_type="user",
        object_id=user.id,
        actor_id=user.id,
        before={"email_domain": previous_email.split("@", 1)[-1]},
        after={"email_domain": row.new_email.split("@", 1)[-1]},
        reason="邮箱换绑确认",
        request_id=request_id,
    )
    await _deliver_mail(
        to=previous_email,
        subject="邮箱已更换",
        body=(
            f"你的账号邮箱已更换为 {row.new_email}。\n"
            "如果不是你本人操作，请立即联系管理员冻结账号。"
        ),
        log_event="email_changed_notice_sent",
        log_extra={"user_id": str(user.id), "request_id": request_id},
    )
    await db.flush()
    return ActionMessage(message="邮箱已更换，请使用新邮箱重新登录")
