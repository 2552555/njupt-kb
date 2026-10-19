"""defense 的输入输出 DTO。

输入与输出分离；输出不返回标记摘要、原始 IP 或任何事件细节中的敏感字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DefenseEventOut",
    "HoneytokenCreateIn",
    "HoneytokenCreatedOut",
    "HoneytokenOut",
    "PolicyCreateIn",
    "PolicyOut",
    "RevokeIn",
    "RiskSubjectOut",
]


class DefenseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    signal: str
    weight: int
    subject_kind: str
    # 假名化主体键；不回传原始 IP 或账号。
    subject_key: str
    request_id: str | None = None
    method: str | None = None
    path_template: str | None = None
    decoy_label: str | None = None
    detail: dict | None = None
    occurred_at: datetime


class RiskSubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_key: str
    subject_kind: str
    score: float
    level: str
    action: str
    action_expires_at: datetime | None = None
    action_reason: str | None = None
    signal_count: int
    last_signal_at: datetime | None = None
    last_signal_kind: str | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    revision: int


class HoneytokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    placement: str
    placement_ref: str | None = None
    expected_visitor: str | None = None
    status: str
    expires_at: datetime | None = None
    retired_at: datetime | None = None
    note: str | None = None
    created_at: datetime


class HoneytokenCreatedOut(BaseModel):
    """明文标记只在下发时返回一次，之后无法再次读取。"""

    token: HoneytokenOut
    raw_token: str
    warning: str = "明文只显示一次；请放入隔离诱饵材料，不要放入真实资料或正常导航。"


class HoneytokenCreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    placement: str = Field(default="test_fixture")
    placement_ref: str | None = Field(default=None, max_length=200)
    expected_visitor: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class RevokeIn(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    mode: str
    suspicious_score: float
    high_score: float
    throttle_factor_suspicious: float
    throttle_factor_high: float
    window_seconds: int
    decay_half_life_seconds: int
    action_ttl_seconds: int
    enabled: bool
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    note: str | None = None
    created_at: datetime


class PolicyCreateIn(BaseModel):
    mode: str = Field(default="observe")
    suspicious_score: float = Field(default=30.0, gt=0, le=1000)
    high_score: float = Field(default=70.0, gt=0, le=1000)
    throttle_factor_suspicious: float = Field(default=0.5, gt=0, le=1)
    throttle_factor_high: float = Field(default=0.25, gt=0, le=1)
    window_seconds: int = Field(default=900, ge=60, le=86400)
    decay_half_life_seconds: int = Field(default=900, ge=60, le=86400)
    action_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class DefenseOverviewOut(BaseModel):
    """只读概览：开关、模式与计数，不返回阈值细节给普通页面。"""

    enabled: bool
    mode: str
    policy_source: str
    events_recent: int
    subjects_suspicious: int
    subjects_high: int
    honeytokens_active: int
