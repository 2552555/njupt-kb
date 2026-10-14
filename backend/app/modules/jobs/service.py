"""jobs 应用服务：业务规则的唯一落点（设计文档 §22、§29、§30、§13.1）。

对外契约（`docs/INTERFACES.md` `## jobs`）：
    enqueue(db, *, job_type, target_id, input_version=1, payload=None, dedupe_key=None) -> Job
    run_job(job_id: str) -> str          # Celery 任务入口，返回 JobStatus 值
    dispatch_due_jobs() -> dict          # beat 扫描器：到期任务、超时租约、事件扇出、幂等清理

规则落点（§22.2）：
1. 幂等约束：目标 + 输入版本 + 处理器版本 + 任务类型；命中既有任务返回既有行，不新建。
2. 输入只传必要 ID，文件内容不通过队列复制。
3. Worker 领取租约 `lease_expires_at = now + settings.JOB_LEASE_SECONDS`，超时可被其他 Worker 重领。
4. 重试只针对临时故障；永久结果直接进 `failed`；达到上限进失败列表，管理员可重试。
5. 输出可识别、可复用；重复执行不生成多个公开版本或重复收益（由幂等约束 + 处理前状态检查保证）。
6. 处理完成时由 content 服务核对资源版本是否仍有效，jobs 不直写 content 的表。
7. 原件安全扫描与 AI 摘要为不同 `JobType`，不共用一个「处理成功」标志。
8. Redis 中断：`enqueue` 只写数据库，不在此处投递；由扫描器重新投递；不宣称「恰好执行一次」。
9. `run_job` 与 `dispatch_due_jobs` 用 `app.db.async_session_factory` 开自己的会话（不在请求上下文）。

跨模块调用一律在函数体内局部导入（`docs/ARCHITECTURE.md` §2.3），不导入对方模型类。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import DerivativeStatus, FailureClass, JobStatus, JobType, ScanStatus
from app.core.errors import (
    Conflict,
    DependencyUnavailable,
    Forbidden,
    InvalidInput,
    NotEnabled,
    NotFound,
    RateLimited,
)
from app.core.pagination import CursorPage, decode_cursor, encode_cursor, normalize_page_size
from app.db import async_session_factory
from app.modules.jobs.models import Job
from app.modules.jobs.schemas import JobFailureOut, JobOut

logger = logging.getLogger("app.jobs")

__all__ = [
    "enqueue",
    "run_job",
    "dispatch_due_jobs",
    "list_jobs",
    "get_job",
    "list_failures",
    "retry_job",
]

# 每个任务类别绑定一个处理器版本，参与幂等约束（§22.2）。
# file.derive 与 content.derivatives.PROCESSOR_VERSION 保持一致，避免重复派生记录。
PROCESSOR_VERSIONS: dict[JobType, str] = {
    JobType.FILE_SCAN: "scan/1",
    JobType.FILE_DERIVE: "derive/1",
    JobType.NOTIFICATION_DISPATCH: "notify/1",
    JobType.EVENT_FANOUT: "fanout/1",
    JobType.AI_PROCESS: "ai/1",
}

# 单次扫描投递/扇出的批量上限，避免一次扫描拉取过多任务。
DISPATCH_BATCH = 200
FANOUT_BATCH = 200

# 退避上限（秒），避免重试间隔无限增长。
MAX_BACKOFF_SECONDS = 3600


class JobPermanentError(Exception):
    """处理器确认的永久失败：格式损坏、安全拒绝等，不重试，直接进 `failed`（§22.2）。"""


class JobTransientError(Exception):
    """处理器确认的临时失败：允许重试（§22.2）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff(attempts: int) -> timedelta:
    base = max(1, settings.JOB_SCAN_INTERVAL_SECONDS)
    seconds = min(base * (2 ** max(0, attempts - 1)), MAX_BACKOFF_SECONDS)
    return timedelta(seconds=seconds)


def _error_message(exc: Exception) -> str:
    detail = str(exc).strip()
    label = type(exc).__name__
    return (f"{label}: {detail}" if detail else label)[:2000]


def _classify(exc: Exception) -> FailureClass:
    """把处理器异常分类为临时/永久故障（§22.2 规则 4）。

    - 明确永久：格式/输入错误、越权、对象不存在、功能未启用。
    - 明确临时：状态冲突（可等待后重试）、依赖不可用、限流、超时、连接错误。
    - 其他未知异常按「可重试」处理，由 `max_attempts` 兜底，避免一次偶发故障直接丢弃任务。
    """
    if isinstance(exc, JobPermanentError):
        return FailureClass.PERMANENT
    if isinstance(exc, JobTransientError):
        return FailureClass.TRANSIENT
    if isinstance(exc, (InvalidInput, Forbidden, NotFound, NotEnabled)):
        return FailureClass.PERMANENT
    if isinstance(exc, (Conflict, DependencyUnavailable, RateLimited)):
        return FailureClass.TRANSIENT
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return FailureClass.TRANSIENT
    return FailureClass.UNKNOWN


def _target_uuid(job: Job) -> uuid.UUID:
    try:
        return uuid.UUID(str(job.target_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise JobPermanentError(f"任务目标不是合法 UUID：{job.target_id}") from exc


# --------------------------------------------------------------------------- #
# 入队：幂等（§22.2 规则 1、8）
# --------------------------------------------------------------------------- #
async def _find_identity(
    db: AsyncSession,
    *,
    job_type: str,
    target_id: str,
    input_version: int,
    processor_version: str,
) -> Job | None:
    stmt = select(Job).where(
        Job.job_type == job_type,
        Job.target_id == target_id,
        Job.input_version == input_version,
        Job.processor_version == processor_version,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def enqueue(
    db: AsyncSession,
    *,
    job_type: JobType,
    target_id: uuid.UUID | str,
    input_version: int = 1,
    payload: dict | None = None,
    dedupe_key: str | None = None,
) -> Job:
    """写入一条任务记录（不做队列投递）。

    命中「目标 + 输入版本 + 处理器版本 + 任务类型」既有任务时返回既有行，不新建（§22.2）。
    只写数据库、不在此处投递：Redis 中断时任务延迟处理，由扫描器重新投递（§22.1、§30）。
    """
    jt = JobType(job_type)
    target = str(target_id)
    processor = PROCESSOR_VERSIONS[jt]

    existing = await _find_identity(
        db,
        job_type=jt.value,
        target_id=target,
        input_version=input_version,
        processor_version=processor,
    )
    if existing is not None:
        return existing

    job = Job(
        job_type=jt.value,
        target_id=target,
        input_version=input_version,
        processor_version=processor,
        status=JobStatus.QUEUED.value,
        attempts=0,
        max_attempts=settings.JOB_MAX_ATTEMPTS,
        next_run_at=_now(),
        dedupe_key=dedupe_key,
        payload=payload or {},
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        # 并发插入命中唯一约束：回读既有任务，不新建（§22.2、§29）。
        existing = await _find_identity(
            db,
            job_type=jt.value,
            target_id=target,
            input_version=input_version,
            processor_version=processor,
        )
        if existing is not None:
            return existing
        raise
    return job


# --------------------------------------------------------------------------- #
# 执行：租约领取 + 处理器注册表（§22.2 规则 3、5、6、7、10）
# --------------------------------------------------------------------------- #
async def _claim(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """条件领取租约；已被领取/已完成时返回 None，保证重复投递不会并发执行。"""
    now = _now()
    result = await db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]),
        )
        .values(
            status=JobStatus.RUNNING.value,
            attempts=Job.attempts + 1,
            started_at=now,
            lease_expires_at=now + timedelta(seconds=settings.JOB_LEASE_SECONDS),
            updated_at=now,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        return None
    return await db.get(Job, job_id)


async def _mark_succeeded(db: AsyncSession, job_id: uuid.UUID) -> None:
    now = _now()
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.SUCCEEDED.value,
            finished_at=now,
            lease_expires_at=None,
            last_error=None,
            failure_class=None,
            updated_at=now,
        )
    )


async def _record_failure(
    db: AsyncSession, job_id: uuid.UUID, exc: Exception, failure_class: FailureClass
) -> str:
    """记录失败；临时故障且未达上限进入 `retry_wait`，否则进入 `failed`（§22.2 规则 4）。"""
    job = await db.get(Job, job_id)
    if job is None:
        return JobStatus.FAILED.value

    now = _now()
    message = _error_message(exc)
    retryable = failure_class != FailureClass.PERMANENT and job.attempts < job.max_attempts
    if retryable:
        job.status = JobStatus.RETRY_WAIT.value
        job.failure_class = failure_class.value
        job.last_error = message
        job.lease_expires_at = None
        job.next_run_at = now + _backoff(job.attempts)
        return JobStatus.RETRY_WAIT.value

    job.status = JobStatus.FAILED.value
    job.failure_class = failure_class.value
    job.last_error = message
    job.finished_at = now
    job.lease_expires_at = None
    return JobStatus.FAILED.value


async def _handle_file_scan(db: AsyncSession, job: Job) -> None:
    """原件安全扫描：与 AI 摘要分属不同任务类别，不共用「处理成功」标志（§22.2 规则 7）。"""
    from app.modules.content.service import process_file_scan

    status = await process_file_scan(db, file_id=_target_uuid(job))
    if status == ScanStatus.PASSED:
        return
    if status == ScanStatus.REJECTED:
        # 检测拒绝属永久结果，不无限重试（§22.2）。
        raise JobPermanentError("文件安全检查拒绝")
    # 检查未能完成（扫描器不可用等）：不得默认通过，属临时故障（§13.1、§30）。
    raise JobTransientError("文件检查未能完成")


async def _handle_file_derive(db: AsyncSession, job: Job) -> None:
    """预览派生；转换失败不改变原件审核事实（§13.1）。"""
    from app.modules.content.service import process_file_derive

    status = await process_file_derive(db, file_id=_target_uuid(job))
    if status == DerivativeStatus.READY:
        return
    # 转换失败多为格式不可解析；不无限自动重试，由管理员修复转换器后重试（§13.1、§30）。
    raise JobPermanentError("预览转换失败")


async def _handle_notification_dispatch(db: AsyncSession, job: Job) -> None:
    from app.modules.notification.service import dispatch_event

    await dispatch_event(db, _target_uuid(job))


async def _handle_event_fanout(db: AsyncSession, job: Job) -> None:
    from app.modules.notification.service import fanout_due_events

    limit = FANOUT_BATCH
    value = (job.payload or {}).get("limit")
    if isinstance(value, int) and value > 0:
        limit = value
    await fanout_due_events(db, limit=limit)


async def _handle_ai_process(db: AsyncSession, job: Job) -> None:
    """AI 处理。处理入口由 ai 模块提供；缺失时显式失败，不返回假成功（§25.2）。"""
    from app.modules.ai import service as ai_service

    process_id = _target_uuid(job)
    runner = getattr(ai_service, "run_process", None)
    if runner is None:
        raise JobPermanentError("AI 处理入口未实现")
    await runner(db, process_id)


async def _execute(db: AsyncSession, job: Job) -> None:
    """按 job_type 分派到处理器；处理器抛异常由调用方分类处理（§22.2 规则 10）。"""
    job_type = JobType(job.job_type)
    if job_type == JobType.FILE_SCAN:
        await _handle_file_scan(db, job)
    elif job_type == JobType.FILE_DERIVE:
        await _handle_file_derive(db, job)
    elif job_type == JobType.NOTIFICATION_DISPATCH:
        await _handle_notification_dispatch(db, job)
    elif job_type == JobType.EVENT_FANOUT:
        await _handle_event_fanout(db, job)
    elif job_type == JobType.AI_PROCESS:
        await _handle_ai_process(db, job)
    else:
        raise JobPermanentError(f"未知任务类型：{job.job_type}")


async def run_job(job_id: str) -> str:
    """Celery 任务入口，返回 JobStatus 值（§22.2）。

    处理器异常在此收敛并写回状态，绝不逃逸到 Celery 之外导致状态卡在 `running`。
    """
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (ValueError, TypeError, AttributeError):
        logger.warning("run_job_invalid_id", extra={"job_id": str(job_id)})
        return JobStatus.FAILED.value

    async with async_session_factory() as db:
        claimed = await _claim(db, job_uuid)
        if claimed is None:
            # 已被其他 Worker 领取、已完成或不可执行：不重复执行（A15）。
            job = await db.get(Job, job_uuid)
            return job.status if job is not None else JobStatus.FAILED.value

        try:
            await _execute(db, claimed)
        except Exception as exc:  # noqa: BLE001 - 处理器异常必须在此收敛
            await db.rollback()
            failure = _classify(exc)
            status = await _record_failure(db, job_uuid, exc, failure)
            await db.commit()
            logger.warning(
                "job_failed",
                extra={
                    "job_id": str(job_uuid),
                    "job_type": claimed.job_type,
                    "failure_class": failure.value,
                    "status": status,
                },
            )
            return status

        # 处理成功：处理器写入的产出与任务状态在同一事务提交。
        await _mark_succeeded(db, job_uuid)
        await db.commit()
        return JobStatus.SUCCEEDED.value


# --------------------------------------------------------------------------- #
# 扫描器：beat 每 JOB_SCAN_INTERVAL_SECONDS 秒调用一次（§22.2 规则 9）
# --------------------------------------------------------------------------- #
async def _reclaim_expired_leases(db: AsyncSession, now: datetime) -> int:
    """回收租约过期的 `running` 任务：放回 `queued` 或按尝试次数进 `failed`（§22.2、A15）。"""
    stmt = select(Job).where(
        Job.status == JobStatus.RUNNING.value,
        Job.lease_expires_at.is_not(None),
        Job.lease_expires_at <= now,
    )
    jobs = list((await db.execute(stmt)).scalars().all())
    for job in jobs:
        job.lease_expires_at = None
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.FAILED.value
            job.failure_class = FailureClass.TRANSIENT.value
            job.last_error = "租约超时且已达最大尝试次数"
            job.finished_at = now
        else:
            job.status = JobStatus.QUEUED.value
            job.next_run_at = now
            job.last_error = "租约超时，已重新排队"
    return len(jobs)


async def _dispatch_due(db: AsyncSession, now: datetime) -> int:
    """投递到期的 `queued`/`retry_wait` 任务（§22.2 规则 9）。"""
    from app.workers.celery_app import submit

    stmt = (
        select(Job)
        .where(
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]),
            Job.next_run_at <= now,
        )
        .order_by(Job.next_run_at.asc(), Job.id.asc())
        .limit(DISPATCH_BATCH)
    )
    jobs = list((await db.execute(stmt)).scalars().all())
    dispatched = 0
    for job in jobs:
        if not submit(str(job.id), job.job_type):
            # 投递失败（Redis 中断等）：保持到期状态，下轮扫描重投（§22.1、§30）。
            continue
        dispatched += 1
        # 投递成功后推迟下一次重投窗口，避免同一任务被反复投递；真正执行由 Worker 领取租约。
        job.status = JobStatus.QUEUED.value
        job.next_run_at = now + timedelta(seconds=max(1, settings.JOB_SCAN_INTERVAL_SECONDS))
    return dispatched


async def _fanout_events() -> int:
    """把待处理域事件转为通知（§21）。通知不可用不影响扫描器其余步骤。"""
    try:
        from app.modules.notification.service import fanout_due_events
    except ImportError:  # pragma: no cover - 模块未就绪时降级
        logger.warning("notification_module_unavailable")
        return 0
    try:
        async with async_session_factory() as db:
            count = await fanout_due_events(db, limit=FANOUT_BATCH)
            await db.commit()
            return int(count or 0)
    except Exception as exc:  # noqa: BLE001 - 扇出失败不回滚其余扫描步骤
        logger.warning("event_fanout_failed", extra={"error": str(exc)})
        return 0


async def _purge_idempotency() -> int:
    from app.core.idempotency import purge_expired

    try:
        async with async_session_factory() as db:
            count = await purge_expired(db)
            await db.commit()
            return int(count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("idempotency_purge_failed", extra={"error": str(exc)})
        return 0


async def dispatch_due_jobs() -> dict:
    """扫描器入口（§22.2 规则 9）。

    返回计数摘要：`leased_reclaimed` / `dispatched` / `events_fanned_out` / `idempotency_purged`。
    """
    summary = {
        "leased_reclaimed": 0,
        "dispatched": 0,
        "events_fanned_out": 0,
        "idempotency_purged": 0,
    }
    now = _now()

    async with async_session_factory() as db:
        summary["leased_reclaimed"] = await _reclaim_expired_leases(db, now)
        await db.commit()

    async with async_session_factory() as db:
        summary["dispatched"] = await _dispatch_due(db, now)
        await db.commit()

    summary["events_fanned_out"] = await _fanout_events()
    summary["idempotency_purged"] = await _purge_idempotency()
    return summary


# --------------------------------------------------------------------------- #
# 管理端读取与重试（§22.2 规则 4：管理员可查看原因并重新执行）
# --------------------------------------------------------------------------- #
async def _page(db: AsyncSession, stmt, *, limit: int | None, cursor: str | None):
    """按 (created_at, id) 降序的游标分页，默认 20、上限 100（§25.1）。"""
    size = normalize_page_size(limit)
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_ts = datetime.fromisoformat(payload["ts"])
            last_id = uuid.UUID(payload["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        stmt = stmt.where(tuple_(Job.created_at, Job.id) < tuple_(last_ts, last_id))
    stmt = stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"ts": last.created_at.isoformat(), "id": str(last.id)})
    return rows, next_cursor, has_more


def _status_values(statuses: list[JobStatus] | None) -> list[str] | None:
    if not statuses:
        return None
    return [JobStatus(item).value for item in statuses]


def _type_values(job_types: list[JobType] | None) -> list[str] | None:
    if not job_types:
        return None
    return [JobType(item).value for item in job_types]


async def list_jobs(
    db: AsyncSession,
    *,
    statuses: list[JobStatus] | None = None,
    job_types: list[JobType] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[JobOut]:
    stmt = select(Job)
    status_values = _status_values(statuses)
    if status_values:
        stmt = stmt.where(Job.status.in_(status_values))
    type_values = _type_values(job_types)
    if type_values:
        stmt = stmt.where(Job.job_type.in_(type_values))

    rows, next_cursor, has_more = await _page(db, stmt, limit=limit, cursor=cursor)
    return CursorPage[JobOut](
        items=[JobOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> JobOut:
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFound("任务不存在")
    return JobOut.model_validate(job)


async def list_failures(
    db: AsyncSession,
    *,
    job_types: list[JobType] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> CursorPage[JobFailureOut]:
    """失败列表与原因（§22.2 规则 4）。"""
    stmt = select(Job).where(Job.status == JobStatus.FAILED.value)
    type_values = _type_values(job_types)
    if type_values:
        stmt = stmt.where(Job.job_type.in_(type_values))

    rows, next_cursor, has_more = await _page(db, stmt, limit=limit, cursor=cursor)
    return CursorPage[JobFailureOut](
        items=[JobFailureOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def retry_job(
    db: AsyncSession, *, job_id: uuid.UUID, actor, request_id: str | None
) -> JobOut:
    """经授权重试可重试任务：重置尝试次数并回到 `queued`（§22.2 规则 4）。

    幂等约束（4 元组）不变，因此复用同一行，不产生重复任务或重复产出。
    投递交由扫描器完成（本函数处于请求事务中，提交前投递会读到未提交行）。
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFound("任务不存在")
    if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        raise Conflict(
            "该任务当前不可重试",
            code="JOB_NOT_RETRYABLE",
            details={"status": job.status},
        )

    now = _now()
    job.status = JobStatus.QUEUED.value
    job.attempts = 0
    job.next_run_at = now
    job.lease_expires_at = None
    job.started_at = None
    job.finished_at = None
    job.failure_class = None
    job.last_error = None
    await db.flush()

    await record_audit(
        db,
        action="job.retried",
        object_type="job",
        object_id=job.id,
        actor_id=actor.id,
        after={"status": JobStatus.QUEUED.value, "job_type": job.job_type},
        request_id=request_id,
    )
    return JobOut.model_validate(job)
