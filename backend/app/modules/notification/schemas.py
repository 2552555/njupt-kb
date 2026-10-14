"""notification 模块 Pydantic DTO（Pydantic v2）。

对外响应一律使用 DTO，不返回 ORM 对象或内部字段（§10）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.enums import NotificationType


class NotificationItem(BaseModel):
    """站内通知条目（§21）。data 为最小必要载荷，不含私密文件信息。"""

    id: uuid.UUID
    type: NotificationType
    title: str
    body: str
    data: dict[str, Any] = {}
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class UnreadCount(BaseModel):
    unread: int


class ReadAllResult(BaseModel):
    updated: int
