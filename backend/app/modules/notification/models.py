"""notification 模块表定义（设计文档 §21）。

`domain_events` 由本模块拥有：`app.core.events.emit_event` 依赖本模块的
`DomainEvent` 类名与列名（event_type / subject_type / subject_id /
subject_version / payload / dedupe_key），不得改名。

P1 只提供可靠站内通知，站内通知本身即投递结果，不额外建投递表。
跨模块外键只引用表名字符串，不导入对方模型类（docs/ARCHITECTURE.md §2）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import DomainEventType, NotificationType
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["DomainEvent", "DomainEventStatus", "Notification"]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """生成状态列 CheckConstraint 的 SQL（§4：String 存枚举值 + CheckConstraint）。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class DomainEventStatus(StrEnum):
    """§21 待处理事件的消费状态。pending 由扫描器消费，failed 保留原因不自动重试。"""

    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class DomainEvent(Base, UUIDPrimaryKey, Timestamped):
    """业务事务内写入的待处理事件；后台据此生成站内通知（§21）。

    消费者按事件 ID 去重：同一条事件只会被一个消费者领取并置为 processed。
    `(event_type, dedupe_key)` 在 dedupe_key 非空时唯一，抑制重复事件堆积。
    """

    __tablename__ = "domain_events"
    __table_args__ = (
        UniqueConstraint(
            "event_type", "dedupe_key", name="uq_domain_events_event_type_dedupe_key"
        ),
        CheckConstraint(_in("event_type", DomainEventType), name="event_type"),
        CheckConstraint(_in("status", DomainEventStatus), name="status"),
        Index("ix_domain_events_status_created", "status", "created_at"),
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    subject_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DomainEventStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base, UUIDPrimaryKey, Timestamped):
    """站内通知（§21）：提交受理、审核结果、版本发布、资源撤下与举报处理结果。"""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_in("type", NotificationType), name="type"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
