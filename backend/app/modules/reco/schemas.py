"""推荐与实验的输入输出 DTO。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BehaviorEventIn",
    "BehaviorReportIn",
    "ConsentIn",
    "ConsentOut",
    "ExperimentCreateIn",
    "ExperimentOut",
    "ModelOut",
    "ProgressIn",
    "ProgressOut",
    "RecoFeedOut",
    "RecoItemOut",
]


class BehaviorEventIn(BaseModel):
    event_type: str = Field(max_length=20)
    resource_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    surface: str | None = Field(default=None, max_length=20)
    position: int | None = Field(default=None, ge=0, le=200)
    dwell_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    progress_pct: int | None = Field(default=None, ge=0, le=100)
    experiment_key: str | None = Field(default=None, max_length=40)
    variant: str | None = Field(default=None, max_length=20)


class BehaviorReportIn(BaseModel):
    session_key: str | None = Field(default=None, max_length=64)
    events: list[BehaviorEventIn] = Field(min_length=1, max_length=50)


class ConsentIn(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=200)


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    version: str
    updated_at: datetime


class ProgressIn(BaseModel):
    progress_pct: int = Field(ge=0, le=100)
    last_position: int | None = Field(default=None, ge=0)
    dwell_ms: int | None = Field(default=None, ge=0, le=3_600_000)


class ProgressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    progress_pct: int
    last_position: int | None = None
    total_dwell_ms: int
    completed_at: datetime | None = None
    updated_at: datetime


class RecoItemOut(BaseModel):
    resource_id: uuid.UUID
    course_id: uuid.UUID
    title: str | None = None
    resource_type: str | None = None
    reason: str
    score: float | None = None


class RecoFeedOut(BaseModel):
    items: list[RecoItemOut]
    experiment: dict | None = None
    model_version: int | None = None
    surface: str


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    kind: str
    status: str
    params: dict | None = None
    metrics: dict | None = None
    sample_size: int
    trained_at: datetime | None = None


class ExperimentCreateIn(BaseModel):
    key: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    variants: list[dict] = Field(min_length=2, max_length=4)
    traffic_bps: int = Field(default=10000, ge=1, le=10000)
    metric: str = Field(default="ctr", max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    status: str
    variants: list | None = None
    metric: str
    traffic_bps: int
    started_at: datetime | None = None
    ended_at: datetime | None = None
    note: str | None = None
