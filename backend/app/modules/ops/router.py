"""运营任务工作台路由（设计文档 §27）。

只做参数解析并调用 `service`；前缀由 `app/main.py` 统一加 `/api/v1`。
工作台是管理员操作台：聚合了失败事件/任务，要求 `ops.view`（当前仅管理员拥有，
不自动授予资料审核员）。安全运营视图要求 `audit.view`：与审计记录同级的安全聚合。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_current_user, get_request_id
from app.core.enums import AccountStatus, Role
from app.core.pagination import CursorPage
from app.core.permissions import AUDIT_VIEW, METRICS_VIEW, OPS_VIEW, USER_SUSPEND, require
from app.modules.ops import accounts, analytics, audit_export, personal
from app.modules.ops import service
from app.modules.ops.schemas import (
    AnalyticsOverviewOut,
    MetricsOut,
    OpsAlertsOut,
    SecurityOverviewOut,
    WorkbenchOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["ops"])


@router.get("/admin/workbench", response_model=WorkbenchOut)
async def get_workbench(
    user: "User" = Depends(require(OPS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> WorkbenchOut:
    return await service.workbench_summary(db)


@router.get("/admin/alerts", response_model=OpsAlertsOut)
async def get_alerts(
    user: "User" = Depends(require(OPS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OpsAlertsOut:
    """运营告警：达到阈值的指标才返回，供管理员优先处置（buchong.md）。"""
    return await service.check_ops_alerts(db)


@router.get("/admin/security/overview", response_model=SecurityOverviewOut)
async def get_security_overview(
    user: "User" = Depends(require(AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> SecurityOverviewOut:
    """安全运营视图：近 1 小时登录失败 Top、近 24 小时新 IP 登录、近 7 天下载速率告警审计的只读聚合。"""
    return await service.security_overview(db)


@router.get("/admin/metrics", response_model=MetricsOut)
async def get_metrics(
    user: "User" = Depends(require(METRICS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> MetricsOut:
    """只读 SLO 指标：连接池预算、缓存统计、事件/任务积压与慢查询阈值（高并发专项 §13）。"""
    return await service.metrics_summary(db)


@router.get("/admin/analytics/overview", response_model=AnalyticsOverviewOut)
async def get_analytics_overview(
    days: int | None = Query(None, description="统计窗口天数，越界由服务层报 InvalidInput"),
    user: "User" = Depends(require(METRICS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOverviewOut:
    """管理端数据分析总览：按 UTC 自然日分桶的产品/运营指标（§43）。

    只读聚合，每个指标一条 GROUP BY 查询；空日期补零，查询数与窗口长度无关。
    """
    return await analytics.analytics_overview(db, days=days)


@router.get("/admin/audit/export")
async def export_audit(
    from_at: datetime | None = Query(None, alias="from", description="起始时间（ISO 8601）"),
    to_at: datetime | None = Query(None, alias="to", description="结束时间（ISO 8601）"),
    action: str | None = Query(None, max_length=80, description="动作前缀匹配，如 review."),
    limit: int | None = Query(None, description="导出上限，超过服务端上限按上限截断"),
    user: "User" = Depends(require(AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> Response:
    """导出审计元数据 CSV（§44）：不含 before/after 原始 JSON 与任何凭据字段。

    默认最近 7 天；超过 ``AUDIT_EXPORT_MAX_ROWS`` 时截断并返回
    ``X-Export-Truncated: true``；导出动作本身写一条 ``audit.export`` 审计。
    """
    result = await audit_export.export_audit_csv(
        db,
        actor_id=user.id,
        from_at=from_at,
        to_at=to_at,
        action=action,
        limit=limit,
        request_id=request_id,
    )
    return Response(
        content=result.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Export-Truncated": "true" if result.truncated else "false",
            "X-Export-Rows": str(result.row_count),
        },
    )


@router.get("/me/overview", response_model=personal.PersonalOverview)
async def personal_overview(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await personal.overview(db, user)


@router.get("/me/downloads", response_model=CursorPage[personal.DownloadHistoryItem])
async def download_history(cursor: str | None = None, limit: int | None = Query(None, ge=1, le=100),
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await personal.downloads(db, user, cursor=cursor, limit=limit)


@router.get("/me/feed", response_model=CursorPage[personal.FeedItem])
async def following_feed(cursor: str | None = None, limit: int | None = Query(None, ge=1, le=100),
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await personal.feed(db, user, cursor=cursor, limit=limit)


@router.get("/admin/users", response_model=CursorPage[accounts.ManagedUser])
async def managed_users(keyword: str | None = Query(None, max_length=120), status: AccountStatus | None = None,
    role: Role | None = None, cursor: str | None = None, limit: int | None = Query(None, ge=1, le=100),
    user=Depends(require(USER_SUSPEND)), db: AsyncSession = Depends(get_db)):
    return await accounts.list_users(db, keyword=keyword, status=status, role=role, cursor=cursor, limit=limit)
