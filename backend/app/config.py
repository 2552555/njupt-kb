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
    # 单账号并发活跃会话上限（beta14）：0 表示不限；登录成功仍放行，仅按 issued_at 淘汰最旧会话。
    SESSION_MAX_ACTIVE_PER_USER: int = Field(default=5, ge=0)

    # --- 对象存储（§16）：私有原件、私有预览、隔离上传区 ---
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    # 用于浏览器直传/下载的可达地址；签名时使用，不能在签名后替换域名。
    S3_PUBLIC_ENDPOINT_URL: str | None = None
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = "njupt"
    S3_SECRET_ACCESS_KEY: str = "njupt-dev-secret"
    S3_BUCKET_QUARANTINE: str = "njupt-quarantine"
    S3_BUCKET_PRIVATE: str = "njupt-private"
    # 头像等公开静态资源桶（设计文档 §43：avatars 走 CDN）。
    S3_BUCKET_PUBLIC: str = "njupt-public"
    # 公开资源的永久访问前缀（CDN 或对外可读的桶地址）；留空时按 endpoint + bucket 拼装。
    S3_PUBLIC_BASE_URL: str | None = None
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

    # --- 头像：直接上传到公开桶，不需要走隔离区扫描（§14.2、§43）---
    AVATAR_MAX_SIZE_BYTES: int = 2 * 1024 * 1024
    ALLOWED_AVATAR_MIME: list[str] = Field(
        default_factory=lambda: ["image/png", "image/jpeg", "image/webp"]
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
    MAX_DOCUMENT_PAGES: int = Field(default=1000, ge=1)
    MAX_IMAGE_PIXELS: int = Field(default=25_000_000, ge=1)
    MAX_ARCHIVE_ENTRIES: int = Field(default=2000, ge=1)
    MAX_ARCHIVE_UNPACKED_BYTES: int = Field(default=250 * 1024 * 1024, ge=1)

    # --- 解析沙箱与网络出口隔离（补充规范 S04、wenjian.md 解析隔离）---
    # off / best-effort / required。默认 best-effort：能隔离就隔离，不能则记录原因；
    # required 时没有网络隔离会拒绝转换，由派生记录如实标记失败。
    PARSE_SANDBOX_MODE: Literal["off", "best-effort", "required"] = "best-effort"
    PARSE_SANDBOX_MEMORY_MB: int = Field(default=1024, ge=128)
    PARSE_SANDBOX_MAX_OUTPUT_MB: int = Field(default=512, ge=16)

    # --- beta11 第一页图片预览与用户水印（prevew.md）---
    # 图片预览默认只渲染第一页：浏览器拿到的是 page1.webp，不是完整 PDF（prevew.md 五）。
    PREVIEW_IMAGE_ENABLED: bool = True
    PREVIEW_IMAGE_WIDTH: int = 1200
    PREVIEW_IMAGE_QUALITY: int = 82
    # 多页图片预览：只渲染前 N 页，避免把整份文档图片化；Office 也走同一链路。
    PREVIEW_IMAGE_MAX_PAGES: int = Field(default=3, ge=1, le=20)
    # 匿名预览水印：匿名用户没有可绑定身份，用固定平台水印并单独限流，不退化为无水印。
    PREVIEW_ANONYMOUS_WATERMARK: str = "访客预览 · 南邮课程知识平台"
    # 水印低可见度但可追溯：斜向重复「用户短码 + 日期」，不用破坏阅读的大水印（prevew.md 十二）。
    PREVIEW_WATERMARK_ENABLED: bool = True
    PREVIEW_WATERMARK_OPACITY: int = 28  # 0-255，Alpha 通道
    # 预览签名 URL 有效期从数分钟级起步，缩短"持有即用的临时凭据"暴露窗口（prevew.md §十二、S05）。
    PREVIEW_URL_TTL_SECONDS: int = 300

    # --- beta11 API 分级限流（pachong.md 三、四）---
    # 第一层规则，按角色分级、可调，不写死；subject 优先取登录用户，其次会话，最后 IP，
    # 避免校园网同一公网 IP 下互相挤占（pachong.md 四）。0 表示不限制。
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_SEARCH: dict[str, int] = {
        "anon": 30, "user": 60, "contributor": 60, "reviewer": 60, "admin": 120,
    }
    RATE_LIMIT_BROWSE: dict[str, int] = {
        "anon": 60, "user": 120, "contributor": 120, "reviewer": 120, "admin": 240,
    }
    RATE_LIMIT_RESOURCE: dict[str, int] = {
        "anon": 60, "user": 120, "contributor": 120, "reviewer": 120, "admin": 240,
    }
    RATE_LIMIT_DOWNLOAD: dict[str, int] = {
        "anon": 5, "user": 10, "contributor": 20, "reviewer": 30, "admin": 60,
    }
    RATE_LIMIT_UPLOAD: dict[str, int] = {
        "anon": 0, "user": 5, "contributor": 20, "reviewer": 30, "admin": 60,
    }
    RATE_LIMIT_LOGIN: dict[str, int] = {
        "anon": 5, "user": 5, "contributor": 5, "reviewer": 5, "admin": 5,
    }

    # --- beta14 下载速率检测（access 域）：按用户维度的次级防护，不替代上面的分级限流 ---
    # 窗口内下载授权次数超过阈值即记录异常速率；数值为可调默认值，非最终运营策略。
    DOWNLOAD_VELOCITY_WINDOW_SECONDS: int = Field(default=300, ge=1)
    DOWNLOAD_VELOCITY_WINDOW_COUNT: int = Field(default=30, ge=1)

    # --- beta14 热门排序时间衰减（catalog/推荐域）：越新的内容热度衰减越慢 ---
    HOT_SCORE_TIME_DECAY_DAYS: int = Field(default=14, ge=1)
    HOT_SCORE_DECAY_POWER: float = Field(default=1.5, gt=0, allow_inf_nan=False)

    # --- beta13 治理队列防滥用（buchong.md）：举报/申诉提交频控与未处理上限 ---
    # 均为可调默认值，不视为最终策略；频控与上限按用户维度，避免单一账号刷爆治理队列。
    COMMENT_REPORT_RATE_LIMIT: int = 10
    COMMENT_REPORT_RATE_WINDOW_SECONDS: int = 3600
    COMMENT_REPORT_OPEN_CAP: int = 20
    COMMENT_APPEAL_RATE_LIMIT: int = 5
    COMMENT_APPEAL_RATE_WINDOW_SECONDS: int = 3600
    COMMENT_APPEAL_OPEN_CAP: int = 5

    # --- beta13 运营告警（buchong.md）：只读聚合阈值，达到即触发告警，不自动处置 ---
    # 阈值均为可调默认值，不视为最终策略；告警只展示，不自动执行封禁/清理/回滚。
    OPS_ALERT_OPEN_REPORTS: int = 50
    OPS_ALERT_PENDING_REVIEWS: int = 30
    OPS_ALERT_OPEN_CORRECTIONS: int = 30
    OPS_ALERT_OPEN_REQUESTS: int = 50
    OPS_ALERT_FAILED_JOBS: int = 20
    OPS_ALERT_FAILED_EVENTS: int = 20
    OPS_ALERT_CRITICAL_MULTIPLIER: int = 3

    # --- beta15 贡献者入驻（用户权责 §3.4、§5）---
    # 同时进行中的入驻申请上限与提交频控；均为可调默认值，不是最终运营策略。
    CONTRIBUTOR_APPLICATION_OPEN_CAP: int = Field(default=1, ge=0)
    CONTRIBUTOR_APPLICATION_RATE_LIMIT: int = Field(default=3, ge=0)
    CONTRIBUTOR_APPLICATION_RATE_WINDOW_SECONDS: int = Field(default=86400, ge=1)

    # --- beta15 案件与侵权通知（用户权责 §13、§16.2）---
    # 侵权通知无需购买即可提交，但仍限频以防滥用；期限按适用法定路径记录，不写死十五日。
    CASE_OPEN_CAP: int = Field(default=10, ge=0)
    CASE_RATE_LIMIT: int = Field(default=10, ge=0)
    CASE_RATE_WINDOW_SECONDS: int = Field(default=3600, ge=1)
    RIGHTS_NOTICE_RATE_LIMIT: int = Field(default=5, ge=0)
    RIGHTS_NOTICE_RATE_WINDOW_SECONDS: int = Field(default=86400, ge=1)
    # 反通知转送权利人后的处理期限（日）。默认沿用电商法第 43 条的十五日口径，
    # 但仅作为记录字段写入 case_events.deadline_at，不代表所有案件统一适用。
    RIGHTS_NOTICE_COUNTER_DEADLINE_DAYS: int = Field(default=15, ge=1)

    # --- beta15 商业化门槛（用户权责 §2.5 G1—G8、§14.4）---
    # 生产收费与贡献者结算在门槛核实前保持关闭；开启后仍受 COMMERCE_ENABLED 约束。
    COMMERCE_COMPLIANCE_GATE_CLOSED: bool = True
    # 分成比例与最低付款额默认 0 表示「未配置」：不套用 80/20 或 85/15 示例值，
    # 未配置时结算/报价接口抛 NotEnabled，不产生任何默认商业条款。
    REVENUE_DEFAULT_CREATOR_BPS: int = Field(default=0, ge=0, le=10000)
    SETTLEMENT_MIN_PAYOUT_FEN: int = Field(default=0, ge=0)

    # --- beta15 缓存与降级（高并发专项 §8、§12）---
    # 进程内有界缓存：Redis 不可用时的回退，且不得放行权限、会话撤销与硬配额。
    CACHE_ENABLED: bool = True
    CACHE_MAX_ENTRIES: int = Field(default=2000, ge=0)
    CACHE_PUBLIC_TTL_SECONDS: int = Field(default=60, ge=1)
    CACHE_VIEWER_TTL_SECONDS: int = Field(default=10, ge=1)
    # 连接池等待上限；用于约束峰值等待而非按 QPS 放大连接数（§3.2）。
    DB_POOL_TIMEOUT_SECONDS: int = Field(default=5, ge=1)
    # 慢查询记录阈值与事件重放上限；只做观测与对账，不自动处置。
    SLOW_QUERY_MS: int = Field(default=300, ge=1)
    EVENT_REPLAY_MAX_ATTEMPTS: int = Field(default=5, ge=1)
    OPS_METRICS_ENABLED: bool = True

    # --- beta16 统一搜索（§22）：建议、热词与查询日志 ---
    # 查询日志只存归一化后的词与结果数，不存用户标识与原始输入，用于热词与零结果观测。
    SEARCH_LOG_ENABLED: bool = True
    SEARCH_QUERY_MAX_CHARS: int = Field(default=80, ge=8, le=120)
    SEARCH_SUGGEST_LIMIT: int = Field(default=6, ge=1, le=20)
    SEARCH_SUGGEST_MIN_CHARS: int = Field(default=1, ge=1, le=10)
    SEARCH_HOT_WINDOW_DAYS: int = Field(default=7, ge=1, le=90)
    SEARCH_HOT_LIMIT: int = Field(default=10, ge=1, le=50)
    SEARCH_LOG_RETENTION_DAYS: int = Field(default=90, ge=1)

    # --- beta16 管理端分析（§43、§45）与审计导出（§44）---
    # 窗口上限与导出上限都是可调默认值；分析只读聚合，导出必写审计并受行数上限约束。
    ANALYTICS_DEFAULT_WINDOW_DAYS: int = Field(default=14, ge=1, le=90)
    ANALYTICS_MAX_WINDOW_DAYS: int = Field(default=90, ge=1, le=365)
    AUDIT_EXPORT_MAX_ROWS: int = Field(default=5000, ge=1, le=20000)

    # --- beta16 通知实时流（§33；buchong 实时推送待办）---
    # SSE 只推「有新通知/未读数变化」的轻量信号，正文仍由受权接口拉取；连接数上限为进程内计数。
    NOTIFICATION_STREAM_ENABLED: bool = True
    NOTIFICATION_STREAM_HEARTBEAT_SECONDS: int = Field(default=25, ge=5, le=60)
    NOTIFICATION_STREAM_MAX_SECONDS: int = Field(default=300, ge=10, le=1800)
    NOTIFICATION_STREAM_MAX_CONNECTIONS: int = Field(default=500, ge=1)

    # --- 0.0.3 主动防御（AI 对抗攻击防御设计 §10.2—§10.6）---
    # 默认 observe：只记录信号、计算风险并留痕，不改变正常请求结果；enforce 才临时收紧限流。
    # 处置一律有有效期、可撤销，且绝不放宽既有固定硬配额。
    DEFENSE_ENABLED: bool = True
    DEFENSE_MODE: Literal["off", "observe", "enforce"] = "observe"
    DEFENSE_WINDOW_SECONDS: int = Field(default=900, ge=60)
    DEFENSE_DECAY_HALF_LIFE_SECONDS: int = Field(default=900, ge=60)
    DEFENSE_SUSPICIOUS_SCORE: float = Field(default=30.0, gt=0)
    DEFENSE_HIGH_SCORE: float = Field(default=70.0, gt=0)
    DEFENSE_THROTTLE_FACTOR_SUSPICIOUS: float = Field(default=0.5, gt=0, le=1)
    DEFENSE_THROTTLE_FACTOR_HIGH: float = Field(default=0.25, gt=0, le=1)
    DEFENSE_ACTION_TTL_SECONDS: int = Field(default=900, ge=60)
    # 单主体单窗口事件上限：超出后只累计计数、不再逐条落库，避免告警风暴拖垮平台（§10.5）。
    DEFENSE_MAX_EVENTS_PER_SUBJECT_WINDOW: int = Field(default=20, ge=1)
    DEFENSE_EVENT_RETENTION_DAYS: int = Field(default=30, ge=1)
    # 蜜罐只注册固定路径，响应固定且无业务能力；不进入 OpenAPI、站点地图与导航。
    HONEYPOT_ENABLED: bool = True
    HONEYPOT_PATH: str = "/api/internal/debug-export"
    HONEYPOT_MAX_BODY_BYTES: int = Field(default=2048, ge=0)
    HONEYPOT_MAX_EVENTS_PER_MINUTE: int = Field(default=60, ge=1)

    # --- 0.0.3 学习行为与推荐（yonghu.md、ALLIN §21/§34）---
    # 采集只记录行为类型、对象、位置、停留分档与进度，不记录正文、输入、IP 或 UA；
    # 用户可在「学习记录」页关闭；保留期到期由清理任务删除。
    BEHAVIOR_COLLECT_ENABLED: bool = True
    BEHAVIOR_RETENTION_DAYS: int = Field(default=180, ge=1)
    RECO_ENABLED: bool = True
    RECO_DEFAULT_LIMIT: int = Field(default=12, ge=1, le=30)
    RECO_TRAIN_MIN_SAMPLES: int = Field(default=50, ge=10)

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
    # 保留期政策未确认时不自动删除；启用后每日执行现有账号与搜索日志清理。
    RETENTION_SCHEDULE_ENABLED: bool = False
    # Fernet 独立随机密钥；不从登录密码派生，也不复用可临时生成的开发 SECRET_KEY。
    MFA_ENCRYPTION_KEY: str = ""
    MFA_REAUTH_MAX_AGE_SECONDS: int = Field(default=600, ge=60, le=3600)
    RATE_LIMIT_MFA: dict[str, int] = {
        "anon": 5, "user": 5, "contributor": 5, "reviewer": 5, "admin": 5,
    }

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
    # --- beta13 AI Gateway 安全基线（rongyu.md、AI对抗攻击防御设计 §6）---
    # AI 当前默认关闭；以下为启用前的 P0 边界：熔断与调用预算。阈值均为可调默认值，
    # 不视为最终生产策略；仅作基础构建块，供未来启用 AI 时直接装配（不擅自接线上供应商）。
    AI_GATEWAY_TIMEOUT_SECONDS: int = 5
    AI_GATEWAY_FAILURE_THRESHOLD: int = 5
    AI_GATEWAY_COOLDOWN_SECONDS: int = 30
    AI_REQUEST_MAX_TOKENS: int = 4000
    AI_REQUEST_MAX_CALLS: int = 4

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
