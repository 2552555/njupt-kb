"""identity HTTP 接口（设计文档 §14、§25.2）。

路由路径不含 /api/v1 前缀，由 app/main.py 统一添加。
本文件只做参数解析与调用 service，业务规则不写在这里。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import (
    get_client_ip,
    get_current_session,
    get_current_user,
    get_db,
    get_request_id,
    parse_uuid,
)
from app.core.pagination import CursorPage
from app.core.security import CSRF_TOKEN_BYTES, generate_token
from app.modules.identity import service
from app.modules.identity.models import Session, User
from app.modules.identity.schemas import (
    ActionMessage,
    CsrfResponse,
    EmailVerificationConfirmRequest,
    EmailVerificationRequest,
    LoginRequest,
    MeResponse,
    MeUpdateRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    SessionItem,
)

router = APIRouter(tags=["identity"])

SESSION_MAX_AGE_SECONDS = settings.SESSION_TTL_HOURS * 3600


def _set_session_cookie(response: Response, token: str) -> None:
    """HttpOnly + Secure + SameSite=Lax：明文令牌只在 Cookie 里，库里只存摘要（§14.2）。"""
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME, path="/", domain=settings.COOKIE_DOMAIN
    )


async def current_session(
    session: Session = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> Session:
    """在已认证请求上节流刷新 last_seen_at；会话有效性仍以 core.deps 的判定为准。"""
    await service.touch_session(db, session)
    return session


@router.post(
    "/auth/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse
)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> RegisterResponse:
    user, sent = await service.register(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        request_id=request_id,
    )
    return RegisterResponse(
        id=user.id,
        email=user.email,
        status=user.status,
        email_verification_sent=sent,
        message="注册成功，请完成邮箱验证后使用",
        note=(
            None
            if sent
            else "开发环境未发送邮件（MAIL_ENABLED=false）：验证链接已写入服务端结构化日志"
        ),
    )


@router.post("/auth/email-verifications", response_model=ActionMessage)
async def request_email_verification(
    payload: EmailVerificationRequest,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    await service.request_email_verification(db, email=payload.email, request_id=request_id)
    # 不区分邮箱是否存在，响应恒等（§14.2）。
    return ActionMessage(message="如果该邮箱需要验证，我们已发送验证邮件")


@router.post("/auth/email-verifications/confirm", response_model=ActionMessage)
async def confirm_email_verification(
    payload: EmailVerificationConfirmRequest,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    await service.confirm_email_verification(db, token=payload.token, request_id=request_id)
    return ActionMessage(
        message="邮箱验证完成。该结果仅表示你当时控制此邮箱，不代表完成官方学生身份认证"
    )


@router.post("/auth/login", response_model=MeResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> MeResponse:
    user, raw_token, _expires_at = await service.login(
        db,
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("User-Agent"),
        client_ip=get_client_ip(request),
        request_id=request_id,
    )
    _set_session_cookie(response, raw_token)
    return await service.me_summary(db, user=user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: Session = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> Response:
    await service.logout(db, session=session, request_id=request_id)
    _clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/password-resets", response_model=ActionMessage)
async def request_password_reset(
    payload: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    await service.request_password_reset(db, email=payload.email, request_id=request_id)
    # 不暴露账号是否存在（§14.2）。
    return ActionMessage(message="如果该邮箱已注册，我们已发送重置说明")


@router.post("/auth/password-resets/confirm", response_model=ActionMessage)
async def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    await service.confirm_password_reset(
        db, token=payload.token, new_password=payload.new_password, request_id=request_id
    )
    return ActionMessage(message="密码已重置，所有登录会话已失效，请重新登录")


@router.get("/auth/csrf", response_model=CsrfResponse)
async def issue_csrf_token(response: Response) -> CsrfResponse:
    """双提交令牌：Cookie 非 HttpOnly，供前端读取后放进 X-CSRF-Token（§14.2）。"""
    token = generate_token(CSRF_TOKEN_BYTES)
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )
    return CsrfResponse(csrf_token=token)


@router.get("/me", response_model=MeResponse)
async def read_me(
    user: User = Depends(get_current_user),
    _session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    return await service.me_summary(db, user=user)


@router.patch("/me", response_model=MeResponse)
async def update_me(
    payload: MeUpdateRequest,
    user: User = Depends(get_current_user),
    _session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> MeResponse:
    return await service.update_me(
        db, user=user, payload=payload.model_dump(exclude_unset=True), request_id=request_id
    )


@router.get("/me/sessions", response_model=CursorPage[SessionItem])
async def list_sessions(
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[SessionItem]:
    return await service.list_sessions(
        db,
        user_id=user.id,
        limit=limit,
        cursor=cursor,
        current_session_id=session.id,
    )


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> Response:
    parsed = parse_uuid(session_id, "session_id")
    await service.revoke_session(
        db, user_id=user.id, session_id=parsed, request_id=request_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
