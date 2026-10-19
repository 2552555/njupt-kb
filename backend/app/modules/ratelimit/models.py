"""ratelimit 模块：API 分级限流的固定窗口计数器（规划设计/pachong.md §三、§四）。

同一公网 IP 下的校园网用户不能互相挤占：subject 优先取登录用户，其次会话，最后 IP。
计数器独立于业务事务提交：请求被拒时业务事务回滚，已发生的计数必须仍然生效。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UUIDPrimaryKey

__all__ = ["RateLimitCounter"]

# 限额档位：与 config.py 的 RATE_LIMIT_* dict key 一一对应（anon 表示未登录）。
TIERS = ("anon", "user", "contributor", "reviewer", "admin")


class RateLimitCounter(Base, UUIDPrimaryKey):
    """固定窗口计数：一个 (动作, subject, 窗口) 一行，窗口内原子自增。

    没有 created_at/updated_at：window_start 本身就是时间标记，热路径上不写额外列。
    过期行由 service.enforce 顺手清理（window_start 上有索引），表不会无限增长。
    """

    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        UniqueConstraint(
            "action",
            "subject_kind",
            "subject",
            "window_start",
            name="uq_rate_limit_counters_window",
        ),
        CheckConstraint(
            "subject_kind IN ('user', 'session', 'ip')",
            name="subject_kind",
        ),
        CheckConstraint("count >= 0", name="count"),
        # 清理过期窗口按 window_start 范围删除；按动作清理/排查按 action。
        Index("ix_rate_limit_counters_window_start", "window_start"),
        Index("ix_rate_limit_counters_action", "action"),
    )

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # user: 用户 UUID 字符串；session: 会话 UUID 字符串；ip: 客户端 IP。都不存令牌或密码。
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    # 对齐到 epoch 的固定窗口起点（UTC），窗口内所有请求共享同一行。
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
