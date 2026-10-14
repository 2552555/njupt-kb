"""应用配置。

设计文档 §31.1：配置分环境管理，启动时检查必要配置，生产环境不能静默使用示例密钥。
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRETS = {
    "",
    "change-me",
    "njupt-kb-dev-secret",
    "njupt-kb-dev-secret-change-in-production",
    "请修改为随机长字符串",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=True
    )

    ENV: Literal["dev", "test", "prod"] = "dev"
    APP_NAME: str = "南邮课程知识平台"
    API_PREFIX: str = "/api/v1"
    PUBLIC_BASE_URL: str = "http://localhost:3000"

    # --- 数据库：本地与部署保持同一语义（§8），统一 PostgreSQL ---
    DATABASE_URL: str = "postgresql+asyncpg://njupt:njupt@localhost:5432/njupt_kb"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10
    DB_ECHO: bool = False

    # --- Redis：缓存与任务投递，不作为唯一业务事实来源（§8）---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- 会话与安全（§14.2）---
    SECRET_KEY: str = ""
    SESSION_COOKIE_NAME: str = "njupt_session"
    SESSION_TTL_HOURS: int = 24 * 14
    SESSION_TOUCH_INTERVAL_MINUTES: int = 30
    CSRF_COOKIE_NAME: str = "njupt_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"
    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: bool | None = None
    LOGIN_RATE_LIMIT_PER_HOUR: int = 20
    LOGIN_FAILURE_LOCKOUT_THRESHOLD: int = 10

    # --- 对象存储（§16）：私有原件、私有预览、隔离上传区 ---
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = "njupt"
    S3_SECRET_ACCESS_KEY: str = "njupt-dev-secret"
    S3_BUCKET_QUARANTINE: str = "njupt-quarantine"
    S3_BUCKET_PRIVATE: str = "njupt-private"
    S3_FORCE_PATH_STYLE: bool = True
    S3_PRESIGN_UPLOAD_SECONDS: int = 900
    S3_PRESIGN_DOWNLOAD_SECONDS: int = 60
    S3_MAX_CONCURRENCY: int = 8

    # --- 上传限制（§16.1，D06 待确认，本版建议值）---
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024
    ALLOWED_UPLOAD_MIME: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "image/png",
            "image/jpeg",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ]
    )

    # --- 内容安全检查（§13.1：扫描不可用不得默认 passed）---
    SCANNER: Literal["clamav", "disabled"] = "clamav"
    CLAMD_HOST: str = "clamav"
    CLAMD_PORT: int = 3310
    SCAN_TIMEOUT_SECONDS: int = 120

    # --- 预览转换（§13.1、§17.1）---
    PREVIEW_MAX_PAGES: int = 3
    PREVIEW_THUMBNAIL_WIDTH: int = 1000
    LIBREOFFICE_BIN: str = "soffice"
    DERIVATIVE_TIMEOUT_SECONDS: int = 180

    # --- 邮件（§21：仅用于验证与找回密码）---
    MAIL_ENABLED: bool = False
    MAIL_FROM: str = "no-reply@example.com"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True

    # --- 任务（§22）---
    JOB_MAX_ATTEMPTS: int = 5
    JOB_LEASE_SECONDS: int = 600
    JOB_SCAN_INTERVAL_SECONDS: int = 30

    # --- 幂等（§25.1：首期建议至少 24 小时）---
    IDEMPOTENCY_TTL_HOURS: int = 24

    # --- CORS：不使用通配符与凭据组合（§28）---
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- 阶段开关（§6）：关闭时接口必须显式不可用，不返回假成功（§25.2）---
    COMMERCE_ENABLED: bool = False
    AI_ENABLED: bool = False
    AI_BASE_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = ""
    AI_MONTHLY_BUDGET_CENTS: int = 0

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if not self.SECRET_KEY:
            if self.ENV == "prod":
                raise ValueError("生产环境必须显式配置 SECRET_KEY")
            self.SECRET_KEY = secrets.token_urlsafe(48)
        elif self.ENV == "prod" and self.SECRET_KEY in INSECURE_SECRETS:
            raise ValueError("生产环境检测到示例 SECRET_KEY，拒绝启动")
        if self.ENV == "prod" and "*" in self.CORS_ORIGINS:
            raise ValueError("生产环境不允许 CORS_ORIGINS 使用通配符")
        if self.ENV == "prod" and not self.COOKIE_SECURE:
            raise ValueError("生产环境必须启用 COOKIE_SECURE")
        if self.AI_ENABLED and not (self.AI_BASE_URL and self.AI_MODEL):
            raise ValueError("AI_ENABLED=true 时必须配置 AI_BASE_URL 与 AI_MODEL")
        return self

    @property
    def cookie_secure(self) -> bool:
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.ENV == "prod"

    @property
    def is_prod(self) -> bool:
        return self.ENV == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
