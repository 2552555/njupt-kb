"""推荐与实验数据模型（ALLIN §21、§34；yonghu.md 推荐与学习行为）。

隐私边界（与《数据安全》§最小化一致）：
- 只记录行为类型、对象、位置、停留时长分档与进度，不记录页面正文、输入内容、IP、UA；
- 主体键为登录用户 ID 或匿名会话键；匿名会话键由服务端生成，不含设备指纹；
- 用户可关闭采集（`behavior_consents`），关闭后不再写入新事件，历史数据按保留期清理；
- 行为数据用于推荐与离线评估，不用于自动处罚或对外共享。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "BehaviorConsent",
    "BehaviorEvent",
    "BehaviorEventType",
    "Experiment",
    "ExperimentAssignment",
    "ExperimentStatus",
    "ReadingProgress",
    "RecommendationModel",
]


class BehaviorEventType(StrEnum):
    IMPRESSION = "impression"
    CLICK = "click"
    VIEW = "view"
    PROGRESS = "progress"
    COMPLETE = "complete"
    SEARCH = "search"
    FAVORITE = "favorite"
    DOWNLOAD = "download"


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    STOPPED = "stopped"
    ARCHIVED = "archived"


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class BehaviorEvent(Base, UUIDPrimaryKey, Timestamped):
    """行为事件（追加写，不做原地更新）。"""

    __tablename__ = "behavior_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", BehaviorEventType), name="behavior_event_type"),
        Index("ix_behavior_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_behavior_events_resource_type", "resource_id", "event_type"),
        Index("ix_behavior_events_occurred", "occurred_at"),
    )

    # 登录用户为空时用 session_key 关联匿名会话；两者至少一个存在（应用层保证）。
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    session_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("resources.id", ondelete="CASCADE"), nullable=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    # 来源界面：home / course / search / detail / feed。
    surface: Mapped[str | None] = mapped_column(String(20), nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwell_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experiment_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(20), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ReadingProgress(Base, UUIDPrimaryKey, Timestamped):
    """阅读进度与累计停留：同一用户同一资源一行。"""

    __tablename__ = "reading_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "resource_id", name="uq_reading_progress_user_resource"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_dwell_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BehaviorConsent(Base, UUIDPrimaryKey, Timestamped):
    """采集同意状态：没有记录时按平台默认（启用）处理，用户可随时关闭。"""

    __tablename__ = "behavior_consents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class RecommendationModel(Base, UUIDPrimaryKey, Timestamped):
    """排序模型版本：参数、样本量与离线指标；同一时间只有一个 active。"""

    __tablename__ = "recommendation_models"
    __table_args__ = (
        UniqueConstraint("version", name="uq_recommendation_models_version"),
        CheckConstraint(
            "status IN ('trained', 'active', 'archived')", name="recommendation_model_status"
        ),
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="logistic")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="trained")
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Experiment(Base, UUIDPrimaryKey, Timestamped):
    """A/B 实验：变体权重用万分比表示，流量也按万分比。"""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("key", name="uq_experiments_key"),
        CheckConstraint(_in("status", ExperimentStatus), name="experiment_status"),
    )

    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExperimentStatus.DRAFT.value
    )
    # [{"key": "control", "weight_bps": 5000}, {"key": "model", "weight_bps": 5000}]
    variants: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metric: Mapped[str] = mapped_column(String(40), nullable=False, default="ctr")
    traffic_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExperimentAssignment(Base, UUIDPrimaryKey, Timestamped):
    """分流结果：同一主体在同一实验内固定同一变体。"""

    __tablename__ = "experiment_assignments"
    __table_args__ = (
        UniqueConstraint("experiment_id", "subject_key", name="uq_experiment_assignments_subject"),
        Index("ix_experiment_assignments_variant", "experiment_id", "variant"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    subject_key: Mapped[str] = mapped_column(String(80), nullable=False)
    variant: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 只用于统计，不参与排序决策。
    exposure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
