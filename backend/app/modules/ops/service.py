"""运营任务工作台服务（设计文档 §27）。

聚合当前待办数量，供管理员一次请求拿到全部跳转入口。只读聚合，不写库、不加缓存、
不建定时任务；每个指标用一条 COUNT 聚合，避免逐条 N+1 查询（§27、§29）。

跨模块只读取其它模块拥有的表：模型类在函数体内局部导入，不在模块顶层导入
（docs/ARCHITECTURE.md §2）。本模块不新增 domain event 类型。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import NotEnabled
from app.modules.ops.schemas import (
    CacheStatsOut,
    DbPoolBudgetOut,
    EventBacklogOut,
    JobBacklogOut,
    MetricsOut,
    OpsAlertOut,
    OpsAlertsOut,
    SecurityLoginFailureItem,
    SecurityNewIpLoginItem,
    SecurityOverviewOut,
    WorkbenchItem,
    WorkbenchOut,
)

# 安全运营视图的统计窗口：管理员安全巡检的固定口径，硬编码为常量而非 config 配置项。
SECURITY_LOGIN_FAILURE_WINDOW_HOURS = 1
SECURITY_NEW_IP_WINDOW_HOURS = 24
SECURITY_VELOCITY_WINDOW_DAYS = 7
SECURITY_LOGIN_FAILURE_TOP_LIMIT = 10
SECURITY_NEW_IP_LIST_LIMIT = 50

UNKNOWN_ACCOUNT_LABEL = "未知账号"


async def _count(db: AsyncSession, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    return int(await db.scalar(stmt) or 0)


async def workbench_summary(db: AsyncSession) -> WorkbenchOut:
    """一次请求内聚合全部待办计数；每条指标一次 COUNT，无逐条查询。"""
    from app.core.enums import (
        CorrectionStatus,
        JobStatus,
        ReportStatus,
        RequestStatus,
        ReviewStatus,
    )
    from app.modules.interaction.models import Correction, ResourceRequest
    from app.modules.jobs.models import Job
    from app.modules.moderation.models import Report, Review
    from app.modules.notification.models import DomainEvent, DomainEventStatus

    # “未处理投诉/举报”指尚未终结（未 resolved / dismissed）的工单（§18）。
    open_report_statuses = [
        ReportStatus.OPEN.value,
        ReportStatus.TRIAGED.value,
        ReportStatus.INVESTIGATING.value,
        ReportStatus.APPEALED.value,
    ]

    pending_reviews = await _count(
        db, Review, Review.status == ReviewStatus.PENDING.value
    )
    open_corrections = await _count(
        db, Correction, Correction.status == CorrectionStatus.OPEN.value
    )
    open_reports = await _count(db, Report, Report.status.in_(open_report_statuses))
    open_requests = await _count(
        db, ResourceRequest, ResourceRequest.status == RequestStatus.OPEN.value
    )
    failed_events = await _count(
        db, DomainEvent, DomainEvent.status == DomainEventStatus.FAILED.value
    )
    failed_jobs = await _count(db, Job, Job.status == JobStatus.FAILED.value)

    items = [
        WorkbenchItem(
            key="pending_reviews",
            label="待审资料版本",
            count=pending_reviews,
            href="/admin/reviews",
        ),
        WorkbenchItem(
            key="open_corrections",
            label="未处理纠错",
            count=open_corrections,
            href="/admin/corrections",
        ),
        WorkbenchItem(
            key="open_reports",
            label="未处理投诉/举报",
            count=open_reports,
            href="/admin/reports",
        ),
        WorkbenchItem(
            key="open_requests",
            label="开放中的求资料需求",
            count=open_requests,
            href="/requests",
        ),
        WorkbenchItem(
            key="failed_tasks",
            label="失败的异步事件/任务",
            count=failed_events + failed_jobs,
            href="/admin/workbench#failed-queues",
        ),
    ]
    return WorkbenchOut(
        items=items,
        total=sum(item.count for item in items),
        generated_at=datetime.now(timezone.utc),
    )


async def check_ops_alerts(db: AsyncSession) -> OpsAlertsOut:
    """只读聚合运营告警：达到阈值的指标才返回，供管理员优先处置（buchong.md）。

    不写库、不加缓存、不自动处置；每条指标一次 COUNT。阈值来自 settings（可调默认值）。
    告警只展示，不触发封禁/清理/回滚等副作用。
    """
    from app.core.enums import (
        CorrectionStatus,
        JobStatus,
        ReportStatus,
        RequestStatus,
        ReviewStatus,
    )
    from app.modules.interaction.models import Correction, ResourceRequest
    from app.modules.jobs.models import Job
    from app.modules.moderation.models import Report, Review
    from app.modules.notification.models import DomainEvent, DomainEventStatus

    # 与工作台一致的口径：未处理举报含 OPEN/TRIAGED/INVESTIGATING/APPEALED（§18）。
    open_report_statuses = [
        ReportStatus.OPEN.value,
        ReportStatus.TRIAGED.value,
        ReportStatus.INVESTIGATING.value,
        ReportStatus.APPEALED.value,
    ]

    pending_reviews = await _count(db, Review, Review.status == ReviewStatus.PENDING.value)
    open_corrections = await _count(
        db, Correction, Correction.status == CorrectionStatus.OPEN.value
    )
    open_reports = await _count(db, Report, Report.status.in_(open_report_statuses))
    open_requests = await _count(
        db, ResourceRequest, ResourceRequest.status == RequestStatus.OPEN.value
    )
    failed_events = await _count(
        db, DomainEvent, DomainEvent.status == DomainEventStatus.FAILED.value
    )
    failed_jobs = await _count(db, Job, Job.status == JobStatus.FAILED.value)

    # (key, label, count, href, warn_threshold)
    metrics = [
        ("open_reports", "未处理举报积压", open_reports, "/admin/reports", settings.OPS_ALERT_OPEN_REPORTS),
        ("pending_reviews", "待审资料版本积压", pending_reviews, "/admin/reviews", settings.OPS_ALERT_PENDING_REVIEWS),
        ("open_corrections", "未处理纠错积压", open_corrections, "/admin/corrections", settings.OPS_ALERT_OPEN_CORRECTIONS),
        ("open_requests", "开放求资料需求积压", open_requests, "/requests", settings.OPS_ALERT_OPEN_REQUESTS),
        ("failed_jobs", "失败异步任务积压", failed_jobs, "/admin/workbench#failed-queues", settings.OPS_ALERT_FAILED_JOBS),
        ("failed_events", "失败领域事件积压", failed_events, "/admin/workbench#failed-queues", settings.OPS_ALERT_FAILED_EVENTS),
    ]

    critical_mult = max(1, settings.OPS_ALERT_CRITICAL_MULTIPLIER)
    alerts: list[OpsAlertOut] = []
    for key, label, count, href, threshold in metrics:
        if threshold <= 0 or count < threshold:
            continue
        level = "critical" if count >= threshold * critical_mult else "warning"
        alerts.append(
            OpsAlertOut(
                key=key,
                label=label,
                count=count,
                href=href,
                level=level,
                threshold=threshold,
            )
        )

    # critical 优先，其次按计数降序，便于管理员聚焦最紧急项。
    alerts.sort(key=lambda a: (a.level != "critical", -a.count))
    return OpsAlertsOut(
        alerts=alerts,
        has_critical=any(a.level == "critical" for a in alerts),
        generated_at=datetime.now(timezone.utc),
    )


async def _emails_by_ids(db: AsyncSession, ids) -> dict:
    """批量回查账号邮箱，避免逐条 N+1 查询（§29）。"""
    id_list = list({i for i in ids if i is not None})
    if not id_list:
        return {}
    from app.modules.identity.models import User

    rows = (await db.execute(select(User.id, User.email).where(User.id.in_(id_list)))).all()
    return {row[0]: row[1] for row in rows}


async def security_overview(db: AsyncSession) -> SecurityOverviewOut:
    """安全运营视图：登录失败 Top、新 IP 登录、下载速率告警审计的只读聚合。

    无写库、无缓存、无定时任务；每块一次聚合查询 + 一次邮箱批量回查（§27、§29）。
    事实来源选择：
    - 登录失败按 actor_id（账号）分组。`identity.login_failed` 审计不记录来源 IP
      （record_audit 未传递 client_ip），IP 维度没有稳定事实来源，故只做账号分组；
      邮箱未注册的失败 actor_id 为空，统一归入「未知账号」。
    - 新 IP 登录以 Session.client_ip（登录时写入的会话事实）为来源：同一用户
      窗口内出现的 client_ip 若在其窗口开始前的历史会话中从未出现，即新 IP。
    """
    from app.modules.identity.models import Session
    from app.modules.moderation.models import AuditLog

    now = datetime.now(timezone.utc)
    failure_cutoff = now - timedelta(hours=SECURITY_LOGIN_FAILURE_WINDOW_HOURS)
    new_ip_cutoff = now - timedelta(hours=SECURITY_NEW_IP_WINDOW_HOURS)
    velocity_cutoff = now - timedelta(days=SECURITY_VELOCITY_WINDOW_DAYS)

    # --- 近 1 小时登录失败（按账号分组） -----------------------------------
    failure_total = await _count(
        db,
        AuditLog,
        AuditLog.action == "identity.login_failed",
        AuditLog.created_at >= failure_cutoff,
    )
    failure_rows = (
        (
            await db.execute(
                select(
                    AuditLog.actor_id,
                    func.count(),
                    func.max(AuditLog.created_at),
                )
                .where(
                    AuditLog.action == "identity.login_failed",
                    AuditLog.created_at >= failure_cutoff,
                )
                .group_by(AuditLog.actor_id)
                # 次数降序；actor_id 作次序键，让 NULL（未知账号）稳定排在末位。
                .order_by(func.count().desc(), AuditLog.actor_id)
                .limit(SECURITY_LOGIN_FAILURE_TOP_LIMIT)
            )
        )
        .all()
    )
    failure_emails = await _emails_by_ids(db, (row[0] for row in failure_rows))
    login_failure_top = [
        SecurityLoginFailureItem(
            account=failure_emails.get(actor_id, UNKNOWN_ACCOUNT_LABEL)
            if actor_id is not None
            else UNKNOWN_ACCOUNT_LABEL,
            account_id=actor_id,
            count=int(count),
            last_failed_at=last_failed_at,
        )
        for actor_id, count, last_failed_at in failure_rows
    ]

    # --- 近 24 小时新 IP 登录（用户 × 历史未出现的 client_ip） ----------------
    window_ip_rows = (
        (
            await db.execute(
                select(Session.user_id, Session.client_ip, func.min(Session.issued_at))
                .where(Session.issued_at >= new_ip_cutoff, Session.client_ip.is_not(None))
                .group_by(Session.user_id, Session.client_ip)
                .order_by(func.min(Session.issued_at).desc())
            )
        )
        .all()
    )
    historical_ips: set[tuple] = set(
        (
            await db.execute(
                select(Session.user_id, Session.client_ip)
                .where(Session.issued_at < new_ip_cutoff, Session.client_ip.is_not(None))
                .distinct()
            )
        ).all()
    )
    new_ip_rows = [
        row for row in window_ip_rows if (row[0], row[1]) not in historical_ips
    ]
    new_ip_emails = await _emails_by_ids(db, (row[0] for row in new_ip_rows))
    new_ip_logins = [
        SecurityNewIpLoginItem(
            account=new_ip_emails.get(user_id, UNKNOWN_ACCOUNT_LABEL),
            account_id=user_id,
            client_ip=client_ip,
            first_seen_at=first_seen_at,
        )
        for user_id, client_ip, first_seen_at in new_ip_rows[:SECURITY_NEW_IP_LIST_LIMIT]
    ]

    # --- 近 7 天下载速率告警审计（action 由下载速率检测写入，本模块只读取） -----
    velocity_count = await _count(
        db,
        AuditLog,
        AuditLog.action == "security.download_velocity_flagged",
        AuditLog.created_at >= velocity_cutoff,
    )

    return SecurityOverviewOut(
        login_failures_last_hour=failure_total,
        login_failure_top=login_failure_top,
        new_ip_login_count=len(new_ip_rows),
        new_ip_logins=new_ip_logins,
        download_velocity_flagged_last_7d=velocity_count,
        generated_at=now,
    )


async def metrics_summary(db: AsyncSession) -> MetricsOut:
    """只读 SLO 指标聚合（高并发专项 §13）：连接池预算、缓存、事件/任务积压与慢查询阈值。

    只读聚合，不写库、不加缓存、不自动处置；``OPS_METRICS_ENABLED=False`` 时显式不可用，
    不返回假成功（§25.2）。事件计数复用 notification.reconcile_events，避免口径漂移。
    """
    if not settings.OPS_METRICS_ENABLED:
        raise NotEnabled("指标端点未启用")

    from app.core.cache import stats as cache_stats
    from app.core.enums import JobStatus
    from app.modules.jobs.models import Job
    from app.modules.notification.reconcile import reconcile_events

    events = await reconcile_events(db)
    job_counts = {
        status.value: await _count(db, Job, Job.status == status.value)
        for status in JobStatus
    }

    return MetricsOut(
        db_pool=DbPoolBudgetOut(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout_seconds=settings.DB_POOL_TIMEOUT_SECONDS,
        ),
        cache=CacheStatsOut(**cache_stats()),
        events=EventBacklogOut(**events),
        jobs=JobBacklogOut(**job_counts, total=sum(job_counts.values())),
        slow_query_ms=settings.SLOW_QUERY_MS,
        generated_at=datetime.now(timezone.utc),
    )
