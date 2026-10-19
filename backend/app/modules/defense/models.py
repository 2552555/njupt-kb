"""主动防御表：蜜罐标记、风险信号事件、主体风险状态与策略版本。

设计依据《AI 对抗攻击防御设计》§10：

- 诱饵只影响平台自有隔离区域，不提供任何真实业务能力（§10.2、§10.6）；
- 事件只保留识别模式所需的最小字段，不存请求体、Cookie、Authorization 或原始查询串（§10.2）；
- 风险状态带窗口、衰减与有效期，可撤销，处置不放宽固定硬配额（§10.5）；
- 策略有版本与有效期，便于回退与复盘（§10.5）。

跨模块外键只写字符串表名（系统设计 §2.2），不导入对方模型类。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

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

from app.core.enums import DefenseSignalKind, HoneytokenStatus, RiskAction, RiskLevel
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "DefenseEvent",
    "DefensePolicy",
    "Honeytoken",
    "RiskSubject",
]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """生成状态列 CheckConstraint 的 SQL（String 存枚举值 + CheckConstraint）。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class Honeytoken(Base, UUIDPrimaryKey, Timestamped):
    """蜜罐标记登记表。

    标记本身**没有**任何业务权限：不能认证、换会话、解密文件、访问对象存储或调用付费服务。
    表里只存摘要，不存可直接使用的明文标记，避免数据库泄露后标记被反用。
    """

    __tablename__ = "honeytokens"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_honeytokens_token_digest"),
        CheckConstraint(_in("status", HoneytokenStatus), name="honeytoken_status"),
        Index("ix_honeytokens_status_expires_at", "status", "expires_at"),
    )

    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # 可读标识，用于告警与复盘；不含标记明文。
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    # 投放位置类别：isolated_material / static_decoy / test_fixture。只描述位置，不存材料正文。
    placement: Mapped[str] = mapped_column(String(40), nullable=False)
    placement_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expected_visitor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=HoneytokenStatus.ACTIVE, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class DefenseEvent(Base, UUIDPrimaryKey, Timestamped):
    """风险信号事件（追加写）。

    `subject_key` 是假名化主体键：登录用户为 `u:<uuid>`、会话为 `s:<uuid>`、
    来源 IP 为 `ip:<sha256 前缀>`。不保存原始 IP、UA 或任何请求正文。
    """

    __tablename__ = "defense_events"
    __table_args__ = (
        CheckConstraint(_in("kind", DefenseSignalKind), name="defense_event_kind"),
        Index("ix_defense_events_subject_occurred", "subject_key", "occurred_at"),
        Index("ix_defense_events_kind_occurred", "kind", "occurred_at"),
        Index("ix_defense_events_occurred", "occurred_at"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 具体信号名，如 "path_enumeration"、"single_param_sweep"；用于统计而非展示给用户。
    signal: Mapped[str] = mapped_column(String(60), nullable=False)
    # 基础权重（1—100）。评分只做加权求和与时间衰减，不引入外部模型（§10.4）。
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    subject_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 路径模板（如 /api/v1/resources/{id}），不含查询串与真实 ID。
    path_template: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 诱饵/标记的可读标识，便于关联投放记录；不含标记明文。
    decoy_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 有限结构化细节：参数类别计数、状态码等；禁止写入正文、Cookie 或签名 URL。
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class RiskSubject(Base, UUIDPrimaryKey, Timestamped):
    """主体风险状态。

    分数随观察窗口衰减，`level` 与 `action` 只是当前窗口的结论；
    `action_expires_at` 到期即恢复，`revoked_at` 支持人工撤销误判（§10.5）。
    """

    __tablename__ = "risk_subjects"
    __table_args__ = (
        UniqueConstraint("subject_key", name="uq_risk_subjects_subject_key"),
        CheckConstraint(_in("level", RiskLevel), name="risk_subject_level"),
        CheckConstraint(_in("action", RiskAction), name="risk_subject_action"),
        Index("ix_risk_subjects_action_expires", "action", "action_expires_at"),
    )

    subject_key: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    level: Mapped[str] = mapped_column(
        String(20), default=RiskLevel.NORMAL, nullable=False
    )
    action: Mapped[str] = mapped_column(
        String(20), default=RiskAction.OBSERVE, nullable=False
    )
    action_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    action_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_signal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_signal_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DefensePolicy(Base, UUIDPrimaryKey, Timestamped):
    """风险策略版本。

    没有有效策略行时使用 settings 默认值；有策略行时以最新有效版本为准，
    便于在不改代码的前提下调整阈值，并保留历史版本用于复盘。
    """

    __tablename__ = "defense_policies"
    __table_args__ = (
        UniqueConstraint("version", name="uq_defense_policies_version"),
        CheckConstraint("mode IN ('observe', 'enforce')", name="defense_policy_mode"),
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="observe")
    suspicious_score: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    high_score: Mapped[float] = mapped_column(Float, nullable=False, default=70.0)
    throttle_factor_suspicious: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    throttle_factor_high: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    decay_half_life_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    action_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
