"""jobs 模块的对外 DTO（设计文档 §10、§25.1）。

只暴露必要字段：不返回 ORM 对象、存储路径、密钥或内部风险参数。
任务 `payload` 仅含必要 ID（如 `file_id`），可由管理员查看。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.enums import FailureClass, JobStatus, JobType


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class JobOut(_ORMModel):
    """任务详情与列表项。"""

    id: uuid.UUID
    job_type: JobType
    target_id: str
    input_version: int
    processor_version: str
    status: JobStatus
    attempts: int
    max_attempts: int
    next_run_at: datetime
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    failure_class: FailureClass | None = None
    dedupe_key: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class JobFailureOut(_ORMModel):
    """失败列表项：管理员可查看原因并重新执行（§22.2）。"""

    id: uuid.UUID
    job_type: JobType
    target_id: str
    input_version: int
    processor_version: str
    attempts: int
    max_attempts: int
    failure_class: FailureClass | None = None
    last_error: str | None = None
    finished_at: datetime | None = None
    updated_at: datetime
