"""审计日志 CSV 导出（设计文档 §44「审计系统」、§27）。

设计取舍：
- 只导出「谁、何时、对什么、做了什么」的元数据列，**不导出 before/after 原始
  JSON**：变更值可能含正文摘要、权益来源等敏感信息，导出文件会脱离系统的访问
  控制流转，一次性外泄不可撤回。
- 邮箱与角色通过 ``users`` 左连接回查（审计表不冗余快照）。因此 ``actor_role``
  是账号**当前**角色，不是操作发生时的历史角色；角色变更本身另有审计记录可追溯。
  账号注销后邮箱按保留期仍在，故仍可导出；查不到账号时两列留空。
- ``action`` 采用**前缀匹配**（``LIKE 'x%'`` 的等价 ``startswith``）：既能精确
  导出单个动作，也能按 ``identity.`` / ``review.`` 前缀导出动作族。前缀中的
  ``%``/``_`` 由 ``startswith`` 自动转义，不构成通配注入。
- CSV 公式注入防护：以 ``=``、``+``、``-``、``@`` 开头（以及制表符/回车开头）
  的值前置单引号，避免 Excel 把导出内容当公式执行（§28 输入按纯文本处理）。
- 导出动作本身写一条 ``audit.export`` 审计，记录筛选条件、行数与是否截断；
  写入失败时不下发文件，返回可重试错误，避免产生无法追踪的导出。
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.errors import DependencyUnavailable, InvalidInput

logger = logging.getLogger(__name__)

# 导出列顺序固定：Excel 打开即可读，不随模型字段变化漂移。
CSV_COLUMNS: tuple[str, ...] = (
    "created_at",
    "action",
    "actor_email",
    "actor_role",
    "target_type",
    "target_id",
    "request_id",
)

DEFAULT_WINDOW_DAYS = 7
# Excel 公式注入的触发字符；制表符/回车可绕过部分版本的前缀检查，一并转义。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class AuditCsvExport:
    """导出结果：CSV 文本（含 BOM）、文件名、行数与截断标记。"""

    content: str
    filename: str
    row_count: int
    truncated: bool


def _aware(value: datetime | None) -> datetime | None:
    """查询参数可能不带时区；统一按 UTC 解释，避免本地时区悄悄改变窗口。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso_utc(value: datetime | None) -> str:
    """时间统一输出为 UTC ISO 8601；SQLite 读出的 naive 值按 UTC 处理。"""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _csv_safe(value: object) -> str:
    """CSV 字段防公式注入：危险前缀加单引号，其余原样（含逗号时由 csv 模块加引号）。"""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _build_csv(rows) -> tuple[str, int]:
    """把查询结果渲染为带 UTF-8 BOM 的 CSV，Excel 打开中文不乱码。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                _csv_safe(_iso_utc(row[0])),
                _csv_safe(row[1]),
                _csv_safe(row[2]),
                _csv_safe(row[3]),
                _csv_safe(row[4]),
                _csv_safe(row[5]),
                _csv_safe(row[6]),
            ]
        )
    return "\ufeff" + buffer.getvalue(), len(rows)


async def export_audit_csv(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    action: str | None = None,
    limit: int | None = None,
    request_id: str | None = None,
) -> AuditCsvExport:
    """导出审计元数据 CSV；行数受 ``AUDIT_EXPORT_MAX_ROWS`` 限制（§44）。

    默认最近 7 天；``to`` 必须晚于 ``from``。``limit`` 超过上限时按上限截断，
    并在响应头标注 ``X-Export-Truncated``。导出后追加 ``audit.export`` 审计，
    写入失败不下发导出文件。
    """
    from app.modules.identity.models import User
    from app.modules.moderation.models import AuditLog

    now = datetime.now(timezone.utc)
    start = _aware(from_at) or (now - timedelta(days=DEFAULT_WINDOW_DAYS))
    end = _aware(to_at) or now
    if end <= start:
        raise InvalidInput("to 必须晚于 from")

    max_rows = settings.AUDIT_EXPORT_MAX_ROWS
    if limit is not None and limit < 1:
        raise InvalidInput("limit 必须为正整数")
    effective_limit = max_rows if limit is None else min(limit, max_rows)
    # 显式请求超过上限即视为截断，即便实际行数更少也如实告知调用方。
    truncated = limit is not None and limit > max_rows

    stmt = (
        select(
            AuditLog.created_at,
            AuditLog.action,
            User.email,
            User.role,
            AuditLog.object_type,
            AuditLog.object_id,
            AuditLog.request_id,
        )
        .select_from(AuditLog)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.created_at >= start, AuditLog.created_at <= end)
        .order_by(AuditLog.created_at, AuditLog.id)
        # 多取一行用于判断是否还有未导出的记录，不改变实际导出条数。
        .limit(effective_limit + 1)
    )
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action, autoescape=True))

    rows = (await db.execute(stmt)).all()
    if len(rows) > effective_limit:
        rows = rows[:effective_limit]
        truncated = True

    content, row_count = _build_csv(rows)

    # 导出动作留痕：记录筛选条件与结果规模，便于事后核对「谁导出过什么范围」。
    try:
        await record_audit(
            db,
            action="audit.export",
            object_type="audit_log",
            actor_id=actor_id,
            after={
                "from": _iso_utc(start),
                "to": _iso_utc(end),
                "action_prefix": action or "",
                "limit": effective_limit,
                "rows": row_count,
                "truncated": truncated,
            },
            request_id=request_id,
        )
        await db.commit()
    except Exception as exc:
        logger.exception("audit.export 审计写入失败")
        await db.rollback()
        raise DependencyUnavailable("审计记录暂不可用，请稍后重试导出") from exc

    filename = f"audit-{now.strftime('%Y%m%d-%H%M%S')}.csv"
    return AuditCsvExport(
        content=content,
        filename=filename,
        row_count=row_count,
        truncated=truncated,
    )
