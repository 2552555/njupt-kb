"""后台任务模型（设计文档 §22.1、§22.2、§13.1）。

本模块拥有 `jobs` 表。

约定：
- 跨模块外键只写字符串表名（`docs/ARCHITECTURE.md` §2.2）。任务的目标是「必要 ID」，
  以 `target_id`（String）保存，不导入目标模块的模型类，也不把文件内容复制进队列（§22.1）。
- 状态列以 `String` 存 `app.core.enums` 的枚举值，并配 `CheckConstraint`（§4）。
- 幂等约束为 `(job_type, target_id, input_version, processor_version)`：
  「目标 + 输入版本 + 处理器版本 + 任务类型」（§22.2）。命中既有任务时 `enqueue` 返回既有行，
  重复执行不会生成多个公开版本或重复收益（§22.2、§29）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.enums import FailureClass, JobStatus, JobType
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["Job"]


def _enum_check(column: str, enum_cls: type, name: str) -> CheckConstraint:
    """由枚举值生成 CheckConstraint，避免字面量与 app.core.enums 漂移。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class Job(Base, UUIDPrimaryKey, Timestamped):
    """一条可恢复的后台任务（§22.1）。

    输入只传必要 ID（`target_id` 与 `payload`），文件内容不通过队列复制。
    租约到期后允许其他 Worker 重新领取（§22.2、A15）。
    """

    __tablename__ = "jobs"
    __table_args__ = (
        # §22.2 幂等约束：目标 + 输入版本 + 处理器版本 + 任务类型。
        UniqueConstraint(
            "job_type",
            "target_id",
            "input_version",
            "processor_version",
            name="uq_jobs_identity",
        ),
        _enum_check("job_type", JobType, "job_type_valid"),
        _enum_check("status", JobStatus, "status_valid"),
        CheckConstraint(
            "failure_class IS NULL OR failure_class IN ("
            + ", ".join(f"'{member.value}'" for member in FailureClass)
            + ")",
            name="failure_class_valid",
        ),
        # §15.2：任务按状态、下次执行时间与租约到期建立索引。
        Index("ix_jobs_status_next_run_at", "status", "next_run_at"),
        Index("ix_jobs_status_lease_expires_at", "status", "lease_expires_at"),
        Index("ix_jobs_job_type_status", "job_type", "status"),
        Index("ix_jobs_dedupe_key", "dedupe_key"),
    )

    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # 目标是必要 ID（多为 UUID 字符串）；不复制文件内容（§22.1）。
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 输入版本参与幂等约束：输入变化形成新任务，不覆盖旧结果（§22.2、§22.2 规则 6）。
    input_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 处理器版本参与幂等约束：处理器升级形成新任务，便于重算（§22.2）。
    processor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=lambda: settings.JOB_MAX_ATTEMPTS
    )
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 领取时写入 now + JOB_LEASE_SECONDS；超时或崩溃后允许其他 Worker 重新领取（§22.2）。
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 可选去重键，供调用方附加语义标识；权威幂等仍以 uq_jobs_identity 为准。
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
