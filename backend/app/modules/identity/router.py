"""identity HTTP 接口（设计文档 §14、§25.2）。

路由路径不含 /api/v1 前缀，由 app/main.py 统一添加。
本文件只做参数解析与调用 service，业务规则不写在这里。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
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
    DeletionRequest,
    EmailChangeConfirmRequest,
    EmailChangeRequestDTO,
    EmailVerificationConfirmRequest,
    EmailVerificationRequest,
    LoginRequest,
    MeResponse,
    MeUpdateRequest,
    PasswordChangeRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    SessionItem,
)
from app.modules.ratelimit.deps import rate_limit

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
    _rl: None = Depends(rate_limit("login")),
) -> MeResponse:
    user, raw_token, _expires_at = await service.login(
        db,
        email=payload.email,
        password=payload.password,
        otp=payload.otp,
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


@router.post("/me/avatar", response_model=MeResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    _session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> MeResponse:
    # 读取上限 +1 字节即可判定超限，避免把任意大小文件读进内存。
    data = await file.read(settings.AVATAR_MAX_SIZE_BYTES + 1)
    return await service.update_avatar(
        db,
        user=user,
        data=data,
        content_type=file.content_type or "",
        request_id=request_id,
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


@router.post("/me/password", response_model=ActionMessage)
async def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    # 复用登录限流：改密接口可枚举旧密码，按同一把「登录」闸刀防爆破。
    _rl: None = Depends(rate_limit("login")),
) -> ActionMessage:
    return await service.change_password(
        db,
        user=user,
        current_session_id=session.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        request_id=request_id,
    )


@router.post("/me/email", response_model=ActionMessage)
async def request_email_change(
    payload: EmailChangeRequestDTO,
    user: User = Depends(get_current_user),
    session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
    _rl: None = Depends(rate_limit("login")),
) -> ActionMessage:
    """发起邮箱换绑：校验当前密码后向新邮箱发送确认令牌（§21 双确认）。"""
    result = await service.request_email_change(
        db,
        user=user,
        session=session,
        new_email=str(payload.new_email),
        password=payload.password,
        request_id=request_id,
    )
    return ActionMessage(message=result.message, mail_sent=settings.MAIL_ENABLED)


@router.post("/me/email/confirm", response_model=ActionMessage)
async def confirm_email_change(
    payload: EmailChangeConfirmRequest,
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    """确认邮箱换绑：令牌一次性、有时效；成功后撤销全部会话（§21）。"""
    result = await service.confirm_email_change(
        db, raw_token=payload.token, request_id=request_id
    )
    return ActionMessage(message=result.message)


@router.post("/me/sessions/revoke-others", response_model=ActionMessage)
async def revoke_other_sessions(
    user: User = Depends(get_current_user),
    session: Session = Depends(current_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    revoked = await service.revoke_other_sessions(
        db, user_id=user.id, current_session_id=session.id, request_id=request_id
    )
    return ActionMessage(
        message="已退出其他设备上的会话" if revoked else "当前没有其他活跃会话"
    )


@router.post("/me/deletion", response_model=ActionMessage)
async def delete_account(
    payload: DeletionRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> ActionMessage:
    """注销账号：匿名化资料并撤销全部会话，业务事实保留（§9）。"""
    result = await service.delete_account(
        db,
        user=user,
        password=payload.password,
        confirm=payload.confirm,
        request_id=request_id,
    )
    _clear_session_cookie(response)
    return result


@router.get("/me/export")
async def export_my_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """导出本人数据为 JSON 附件（§9）：不含文件字节、签名 URL 与他人数据。"""
    payload = await service.export_my_data(db, user=user)
    filename = f"njupt-kb-export-{date.today().isoformat()}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
