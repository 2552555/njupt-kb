"""运营任务工作台 DTO（设计文档 §27）。

对外只返回计数与跳转路径，不返回工单内容、操作者或敏感载荷。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class WorkbenchItem(BaseModel):
    """单个待办指标：名称、数量与可跳转的前台/后台路径。"""

    key: str
    label: str
    count: int
    href: str


class WorkbenchOut(BaseModel):
    """工作台一次请求返回全部待办计数，避免逐条 N+1 查询。"""

    items: list[WorkbenchItem]
    total: int
    generated_at: datetime


class OpsAlertOut(BaseModel):
    """单条运营告警：达到阈值的指标、当前计数与可跳转路径。"""

    key: str
    label: str
    count: int
    href: str
    level: str  # "warning" | "critical"
    threshold: int


class OpsAlertsOut(BaseModel):
    """运营告警列表：只读聚合，达到阈值的指标才出现，供管理员优先处置。"""

    alerts: list[OpsAlertOut]
    has_critical: bool
    generated_at: datetime


class SecurityLoginFailureItem(BaseModel):
    """单个账号近 1 小时的登录失败聚合（按账号分组）。

    `identity.login_failed` 审计不记录 client_ip（record_audit 未传递来源 IP），
    因此失败维度只按账号聚合，没有 IP 字段。
    """

    account: str  # 账号邮箱；未匹配到用户的失败归入「未知账号」
    account_id: uuid.UUID | None
    count: int
    last_failed_at: datetime | None


class SecurityNewIpLoginItem(BaseModel):
    """近 24 小时内从该账号历史未出现过的 client_ip 发起的登录（按用户×IP 去重）。"""

    account: str  # 账号邮箱；账号已注销时回退为「未知账号」
    account_id: uuid.UUID
    client_ip: str
    first_seen_at: datetime


class SecurityOverviewOut(BaseModel):
    """安全运营视图：只读聚合，无缓存、无副作用（设计文档 §27）。"""

    login_failures_last_hour: int  # 窗口内登录失败总数（含 Top 之外）
    login_failure_top: list[SecurityLoginFailureItem]  # 最多 10 个账号
    new_ip_login_count: int  # 近 24 小时新 IP 登录（用户×IP 去重）
    new_ip_logins: list[SecurityNewIpLoginItem]  # 明细，最多 50 条
    download_velocity_flagged_last_7d: int  # 近 7 天下载速率告警审计条数
    generated_at: datetime


class DbPoolBudgetOut(BaseModel):
    """连接池预算（高并发专项 §3.2、§7.3）：只读展示，不在此处调参。"""

    pool_size: int
    max_overflow: int
    pool_timeout_seconds: int


class CacheClassStatsOut(BaseModel):
    """单类缓存的条目与命中/未命中计数（高并发专项 §8.1）。"""

    entries: int
    hits: int
    misses: int


class CacheStatsOut(BaseModel):
    """进程内缓存统计：有界条目数、命中/未命中/淘汰/失效与按分类分布（§8.2、§13）。"""

    enabled: bool
    entries: int
    max_entries: int
    hits: int
    misses: int
    evictions: int
    invalidations: int
    by_class: dict[str, CacheClassStatsOut]


class EventBacklogOut(BaseModel):
    """域事件积压：复用 notification.reconcile_events 的只读计数（§10.2、§12）。"""

    pending: int
    processing: int
    processed: int
    failed: int
    over_max_attempts: int
    duplicate_dedupe_keys: int


class JobBacklogOut(BaseModel):
    """任务积压：jobs 表按 JobStatus 计数（§13.1、§22）。"""

    queued: int
    running: int
    retry_wait: int
    succeeded: int
    failed: int
    cancelled: int
    total: int


class MetricsOut(BaseModel):
    """只读 SLO 指标聚合：连接池预算、缓存、事件/任务积压与慢查询阈值。

    只读聚合，不写库、不自动处置（高并发专项 §13）。
    """

    db_pool: DbPoolBudgetOut
    cache: CacheStatsOut
    events: EventBacklogOut
    jobs: JobBacklogOut
    slow_query_ms: int
    generated_at: datetime


class AnalyticsCounts(BaseModel):
    """单个统计口径的计数集合，供按日分桶与窗口汇总共用（§43）。

    字段即对外指标名，前端可直接按同名字段画图；口径见 `ops.analytics` 的 docstring。
    """

    new_users: int
    new_resources: int
    published_versions: int
    downloads: int
    favorites: int
    ratings: int
    comments: int
    reports: int
    corrections: int
    requests: int
    cases: int


class AnalyticsPoint(AnalyticsCounts):
    """一天的分桶计数。窗口内没有数据的日期也返回 0，避免前端折线断点。"""

    date: str  # UTC 自然日，YYYY-MM-DD


class AnalyticsFunnelOut(BaseModel):
    """窗口内转化漏斗：上传会话 → 提交版本 → 审核通过 → 发布 → 下载授权。

    各阶段口径不同、分母不同，只做绝对量展示，不计算「用户转化率」（§43）。
    """

    uploaded: int
    submitted: int
    approved: int
    published: int
    downloaded: int


class TopCourseItem(BaseModel):
    """窗口内下载授权数领先的课程：只暴露课程 id/名称与下载数。"""

    course_id: uuid.UUID
    name: str
    download_count: int


class AnalyticsOverviewOut(BaseModel):
    """管理端数据分析总览：只读聚合，不写库、不建定时任务（§43）。"""

    window_days: int
    generated_at: datetime
    series: list[AnalyticsPoint]
    totals: AnalyticsCounts
    funnel: AnalyticsFunnelOut
    top_courses: list[TopCourseItem]
    data_notes: list[str]
