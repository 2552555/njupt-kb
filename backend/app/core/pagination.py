"""游标分页（设计文档 §25.1）。

列表统一返回 items / next_cursor / has_more，默认每页 20、上限 100。
游标对客户端不透明；无效游标返回输入错误，不让解码异常变成 500。
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import and_, func, or_

from app.core.errors import InvalidInput

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


def normalize_page_size(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if limit < 1:
        raise InvalidInput("limit 必须为正整数")
    return min(limit, MAX_PAGE_SIZE)


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
    if not isinstance(payload, dict):
        raise InvalidInput("cursor 无效", details={"reason": "malformed"})
    return payload


def seek_descending(db, stmt, timestamp, identity, cursor):
    """按同一时间表达式排序/翻页，兼容 SQLite 默认时间的小数位差异。"""
    sqlite = db.bind.dialect.name == "sqlite"
    stamp = func.strftime("%Y-%m-%d %H:%M:%f", timestamp) if sqlite else timestamp
    stmt = stmt.order_by(stamp.desc(), identity.desc())
    if not cursor:
        return stmt
    try:
        payload = decode_cursor(cursor)
        moment = datetime.fromisoformat(payload["ts"])
        item_id = uuid.UUID(payload["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidInput("cursor 无效") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    point = func.strftime("%Y-%m-%d %H:%M:%f", moment) if sqlite else moment
    return stmt.where(or_(stamp < point, and_(stamp == point, identity < item_id)))
