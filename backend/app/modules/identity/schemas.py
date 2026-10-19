"""identity 对外 DTO（设计文档 §10、§25.1）。

对外响应一律用 DTO，不暴露 password_hash、token_digest 等内部字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = Field(default=None, max_length=80)


class RegisterResponse(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    email_verification_sent: bool
    message: str
    # MAIL_ENABLED=false 时明确说明邮件未发送，不假装已发送（§21）。
    note: str | None = None


class EmailVerificationRequest(BaseModel):
    email: EmailStr


class EmailVerificationConfirmRequest(BaseModel):
    token: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    otp: str | None = Field(default=None, max_length=64)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=300)
    new_password: str = Field(min_length=1, max_length=300)


class EmailChangeRequestDTO(BaseModel):
    """发起邮箱换绑：需当前密码确认，确认链接发往新邮箱（0.0.3）。"""

    new_email: EmailStr
    password: str = Field(min_length=1, max_length=300)


class EmailChangeConfirmRequest(BaseModel):
    """确认邮箱换绑：令牌一次性、有时效，确认后旧会话全部失效。"""

    token: str = Field(min_length=1)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str


class ActionMessage(BaseModel):
    """无副作用的通用结果说明；不暴露账号是否存在（§14.2）。"""

    message: str
    mail_sent: bool = False
    note: str | None = None


class CsrfResponse(BaseModel):
    csrf_token: str


class MeResponse(BaseModel):
    """当前账号与必要权益摘要（§25.2）。"""

    id: uuid.UUID
    email: str
    role: str
    permissions: list[str] = Field(default_factory=list)
    status: str
    display_name: str
    avatar_url: str | None = None
    gender: str | None = None
    bio: str | None = None
    email_verified: bool
    # 校园身份校验方案确定前恒为 False，不宣称完成官方学生身份认证（D03、§15.1）。
    school_verified: bool
    email_verified_at: datetime | None = None
    college_id: uuid.UUID | None = None
    major_id: uuid.UUID | None = None
    enrollment_year: int | None = None
    preferences: dict = Field(default_factory=dict)
    created_at: datetime


class MeUpdateRequest(BaseModel):
    """PATCH 语义：字段缺席表示不改，显式 null 表示清空（display_name 除外）。"""

    display_name: str | None = Field(default=None, max_length=80)
    avatar_url: str | None = Field(default=None, max_length=500)
    gender: Literal["male", "female", "nonbinary", "undisclosed"] | None = None
    bio: str | None = Field(default=None, max_length=500)
    college_id: uuid.UUID | None = None
    major_id: uuid.UUID | None = None
    enrollment_year: int | None = Field(default=None, ge=1900, le=2100)
    preferences: dict | None = None

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar(cls, value: str | None) -> str | None:
        if not value:
            return None
        url = HttpUrl(value)
        if url.scheme != "https" or url.username or url.password:
            raise ValueError("头像需要不含账号密码的 HTTPS 图片地址")
        return str(url)


class SessionItem(BaseModel):
    id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    user_agent: str | None = None
    client_ip: str | None = None
    current: bool = False


class DeletionRequest(BaseModel):
    """注销账号请求（§9）。确认词是否匹配由 service 判定，避免误触。"""

    password: str
    confirm: str = Field(min_length=1, max_length=40)

