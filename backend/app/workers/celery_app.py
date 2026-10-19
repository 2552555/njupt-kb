"""Celery 应用（设计文档 §8、§22）。

Redis 只承担调度与投递，任务事实保存在数据库。数据库写入成功但投递失败的任务，
由 beat 上的扫描器重新投递；不宣称队列提供"恰好执行一次"。
"""

from __future__ import annotations

import asyncio
import logging

from celery import Celery
from celery.schedules import schedule

from app.config import settings
from app.core.enums import JobType
from app.core.errors import NotEnabled

logger = logging.getLogger("app.worker")

TASK_QUEUES = {
    JobType.FILE_SCAN: "njupt.scan",
    JobType.FILE_DERIVE: "njupt.derive",
    JobType.NOTIFICATION_DISPATCH: "njupt.notify",
    JobType.EVENT_FANOUT: "njupt.notify",
    JobType.AI_PROCESS: "njupt.ai",
}

celery_app = Celery("njupt_kb", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="njupt",
    task_routes={kind.value: {"queue": queue} for kind, queue in TASK_QUEUES.items()},
    broker_connection_retry_on_startup=True,
    task_time_limit=settings.DERIVATIVE_TIMEOUT_SECONDS + 120,
    task_soft_time_limit=settings.DERIVATIVE_TIMEOUT_SECONDS + 60,
    result_expires=3600,
    beat_schedule={
        "dispatch-due-jobs": {
            "task": "jobs.dispatch_due",
            "schedule": schedule(run_every=settings.JOB_SCAN_INTERVAL_SECONDS),
        },
    },
)

if settings.RETENTION_SCHEDULE_ENABLED:
    celery_app.conf.beat_schedule["retention-cleanup"] = {
        "task": "maintenance.retention", "schedule": schedule(run_every=86400),
    }


def _run(coro):
    """在同步 Celery worker 中执行协程。"""
    return asyncio.run(coro)


def _run_job(job_id: str) -> str:
    from app.modules.jobs.service import run_job

    return _run(run_job(job_id))


@celery_app.task(name="jobs.dispatch_due", bind=True, max_retries=0)
def dispatch_due_jobs(self) -> dict:
    """扫描器：领取到期的排队任务与超时租约，重新投递（§22.1、§30）。"""
    from app.modules.jobs.service import dispatch_due_jobs as _dispatch

    return _run(_dispatch())


@celery_app.task(name=JobType.FILE_SCAN.value, bind=True, max_retries=0)
def file_scan(self, job_id: str) -> str:
    return _run_job(job_id)


@celery_app.task(name=JobType.FILE_DERIVE.value, bind=True, max_retries=0)
def file_derive(self, job_id: str) -> str:
    return _run_job(job_id)


@celery_app.task(name=JobType.NOTIFICATION_DISPATCH.value, bind=True, max_retries=0)
def notification_dispatch(self, job_id: str) -> str:
    return _run_job(job_id)


@celery_app.task(name=JobType.EVENT_FANOUT.value, bind=True, max_retries=0)
def event_fanout(self, job_id: str) -> str:
    return _run_job(job_id)


@celery_app.task(name=JobType.AI_PROCESS.value, bind=True, max_retries=0)
def ai_process(self, job_id: str) -> str:
    return _run_job(job_id)


async def run_retention_cleanup() -> dict[str, int]:
    if not settings.RETENTION_SCHEDULE_ENABLED:
        raise NotEnabled("自动保留期清理未启用")
    from app.modules.identity.retention import _run as clean_accounts
    from app.modules.search.retention import _run as clean_search

    return {"accounts": await clean_accounts(), "search_queries": await clean_search()}


@celery_app.task(name="maintenance.retention", max_retries=0)
def retention_cleanup() -> dict[str, int]:
    return _run(run_retention_cleanup())


TASKS_BY_TYPE = {
    JobType.FILE_SCAN: file_scan,
    JobType.FILE_DERIVE: file_derive,
    JobType.NOTIFICATION_DISPATCH: notification_dispatch,
    JobType.EVENT_FANOUT: event_fanout,
    JobType.AI_PROCESS: ai_process,
}


def submit(job_id: str, job_type: str) -> bool:
    """投递任务；失败返回 False，由扫描器后续重投（§22.1）。"""
    task = TASKS_BY_TYPE.get(JobType(job_type))
    if task is None:
        logger.error("unknown_job_type", extra={"job_type": job_type, "job_id": job_id})
        return False
    try:
        task.apply_async(args=[job_id], queue=TASK_QUEUES[JobType(job_type)])
        return True
    except Exception as exc:  # noqa: BLE001 - 投递失败不抛出，交由扫描器重投
        logger.warning("job_submit_failed", extra={"job_id": job_id, "error": str(exc)})
        return False
