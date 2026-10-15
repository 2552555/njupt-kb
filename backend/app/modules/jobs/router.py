"""jobs 路由（设计文档 §25.2、§27）。

只做参数解析与调用 `service`；业务规则不在这里。
前缀由 `app/main.py` 统一加 `/api/v1`，本模块不自行决定前缀。

权限：
- 读取（列表/详情/失败列表）用 `require(AUDIT_VIEW)`；admin 拥有全部权限亦覆盖 `job.retry`。
- 重试用 `require(JOB_RETRY)`（§22.2 规则 4：经授权重试可重试任务）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_request_id
from app.core.enums import JobStatus, JobType
from app.core.pagination import CursorPage
from app.core.permissions import AUDIT_VIEW, JOB_RETRY, require
from app.modules.jobs import service
from app.modules.jobs.schemas import JobFailureOut, JobOut

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter()


# 注意：`/admin/jobs/failures` 必须先于 `/admin/jobs/{job_id}` 声明，避免被路径参数吞掉。
@router.get("/admin/jobs/failures", response_model=CursorPage[JobFailureOut])
async def list_job_failures(
    job_type: list[JobType] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[JobFailureOut]:
    return await service.list_failures(db, job_types=job_type, limit=limit, cursor=cursor)


@router.get("/admin/jobs", response_model=CursorPage[JobOut])
async def list_jobs(
    job_status: list[JobStatus] | None = Query(default=None, alias="status"),
    job_type: list[JobType] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[JobOut]:
    return await service.list_jobs(
        db, statuses=job_status, job_types=job_type, limit=limit, cursor=cursor
    )


@router.get("/admin/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    user: "User" = Depends(require(AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    return await service.get_job(db, job_id)


@router.post(
    "/admin/jobs/{job_id}/retry",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_job(
    job_id: uuid.UUID,
    user: "User" = Depends(require(JOB_RETRY)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    return await service.retry_job(db, job_id=job_id, actor=user, request_id=request_id)
