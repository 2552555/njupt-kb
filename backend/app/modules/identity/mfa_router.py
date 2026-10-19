"""Authenticated MFA setup and step-up; no seed/recovery material in audits."""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_session, get_current_user, get_db, get_request_id
from app.modules.identity import mfa
from app.modules.identity.router import _set_session_cookie
from app.modules.ratelimit.deps import rate_limit

router = APIRouter(tags=["identity"])


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=300)


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    otp: str = Field(min_length=1, max_length=64)


class ReauthIn(PasswordIn):
    otp: str = Field(min_length=1, max_length=64)


class MFAStatus(BaseModel):
    enabled: bool
    configured: bool
    recovery_remaining: int
    recently_verified: bool


class SetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryOut(BaseModel):
    recovery_codes: list[str]


@router.get("/me/mfa", response_model=MFAStatus)
async def get_mfa(
    user=Depends(get_current_user),
    session=Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    return await mfa.status(db, user, session)


@router.post(
    "/me/mfa/setup", response_model=SetupOut, dependencies=[Depends(rate_limit("mfa"))]
)
async def setup_mfa(
    body: PasswordIn,
    response: Response,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    trace=Depends(get_request_id),
):
    response.headers["Cache-Control"] = "no-store"
    return await mfa.begin_setup(db, user, body.password, trace)


@router.post(
    "/me/mfa/confirm",
    response_model=RecoveryOut,
    dependencies=[Depends(rate_limit("mfa"))],
)
async def confirm_mfa(
    body: VerifyIn,
    response: Response,
    user=Depends(get_current_user),
    session=Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
    trace=Depends(get_request_id),
):
    codes, raw = await mfa.confirm_setup(db, user, session, body.otp, trace)
    _set_session_cookie(response, raw)
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_codes": codes}


@router.post("/me/mfa/reauth", dependencies=[Depends(rate_limit("mfa"))])
async def reauth_mfa(
    body: ReauthIn,
    response: Response,
    user=Depends(get_current_user),
    session=Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
    trace=Depends(get_request_id),
):
    raw = await mfa.reauthenticate(db, user, session, body.password, body.otp, trace)
    _set_session_cookie(response, raw)
    return {"message": "已重新认证，其他设备会话已撤销"}


@router.post("/me/mfa/disable", dependencies=[Depends(rate_limit("mfa"))])
async def disable_mfa(
    body: ReauthIn,
    response: Response,
    user=Depends(get_current_user),
    session=Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
    trace=Depends(get_request_id),
):
    raw = await mfa.disable(db, user, session, body.password, body.otp, trace)
    _set_session_cookie(response, raw)
    return {"message": "验证器已禁用，其他设备会话已撤销"}
