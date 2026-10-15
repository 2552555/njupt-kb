"""ai 表定义（设计文档 §23、§13.2）。

三张表构成处理事实、预算与用量：
- `ai_processes`：一次 AI 处理的输入版本、模型标识、提示模板版本、状态与结果。
- `ai_budgets`：单用户与总预算（按周期计），金额用整数最小货币单位，禁用浮点（§10）。
- `ai_usages`：每次处理的 token 与成本记录，成本缺失时记 0 并在结果中标注"成本未知"。

跨模块外键只用字符串表名（docs/ARCHITECTURE.md §2.2），不导入对方模型类。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AIProcessKind, AIProcessStatus
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["AIProcess", "AIBudget", "AIUsage", "BudgetScope"]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """生成状态列 CheckConstraint 的 SQL（§4：String 存枚举值 + CheckConstraint）。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class BudgetScope(StrEnum):
    """预算作用域。scope_id 为空表示总预算。"""

    GLOBAL = "global"
    USER = "user"


class AIProcess(Base, UUIDPrimaryKey, Timestamped):
    """一次 AI 处理记录。

    只记录与展示事实，不承载业务状态变更：模型不得据此删除内容、封禁用户或调整权益（§23）。
    """

    __tablename__ = "ai_processes"
    __table_args__ = (
        CheckConstraint(_in("kind", AIProcessKind), name="kind"),
        CheckConstraint(_in("status", AIProcessStatus), name="status"),
        # 复用键（§23）：内容与模型版本相同的结果可复用，但遵守权限隔离。
        Index(
            "ix_ai_processes_reuse_key",
            "resource_version_id",
            "kind",
            "model",
            "prompt_template_version",
        ),
        Index("ix_ai_processes_status_created", "status", "created_at"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id"), nullable=False, index=True
    )
    # 复用键的首列，已由 ix_ai_processes_reuse_key 覆盖，不重复建单列索引（§15.2）。
    resource_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resource_versions.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AIProcessStatus.QUEUED.value
    )
    # 可空：系统触发的处理没有请求者；匿名用户不得触发会外发内容的处理。
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True, index=True
    )
    # 模型标识与提示模板版本必须随结果记录（§23）。
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    prompt_template_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 自引用：本次结果复用了哪一次处理（§23 复用键）。
    reuse_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ai_processes.id"), nullable=True
    )


class AIBudget(Base, UUIDPrimaryKey, Timestamped):
    """单用户与总预算（§23）。金额为整数最小货币单位（§10）。"""

    __tablename__ = "ai_budgets"
    __table_args__ = (
        CheckConstraint(_in("scope", BudgetScope), name="scope"),
        # 用户级预算：同一用户同一周期只有一条。
        UniqueConstraint("scope", "scope_id", "period_start", name="scope_scope_id_period_start"),
        # 总预算：scope_id 为空，PostgreSQL 视 NULL 互不相同，需部分唯一索引兜底。
        Index(
            "uq_ai_budgets_global_period",
            "period_start",
            unique=True,
            postgresql_where=text("scope = 'global'"),
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    limit_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AIUsage(Base, UUIDPrimaryKey, Timestamped):
    """单次处理的用量记录。token 与成本按模型响应中的 usage 字段记录（§23）。"""

    __tablename__ = "ai_usages"

    process_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_processes.id"), nullable=False, index=True
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 成本缺失时记 0，并在结果中标注"成本未知"，不编造价格。
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
