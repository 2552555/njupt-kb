"""账号治理查询；状态变更继续由 moderation 持有。"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import or_, select

from app.core.enums import AccountStatus, Role
from app.core.pagination import normalize_page_size, seek_descending
from app.modules.ops.personal import result_page


class ManagedUser(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    status: AccountStatus
    email_verified: bool
    created_at: datetime


async def list_users(db, *, keyword=None, status=None, role=None, cursor=None, limit=None):
    from app.modules.identity.models import User, UserProfile

    size = normalize_page_size(limit)
    stmt = select(User.id, User.email, User.role, User.status, User.created_at,
                  User.email_verified_at, UserProfile.display_name).outerjoin(UserProfile, UserProfile.user_id == User.id)
    if keyword:
        pattern = "%" + keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(User.email.ilike(pattern, escape="\\"), UserProfile.display_name.ilike(pattern, escape="\\")))
    if status:
        stmt = stmt.where(User.status == status)
    if role:
        stmt = stmt.where(User.role == role)
    stmt = seek_descending(db, stmt, User.created_at, User.id, cursor)
    rows = (await db.execute(stmt.limit(size + 1))).all()
    items = [ManagedUser(id=row.id, email=row.email, role=row.role, status=row.status,
        created_at=row.created_at, display_name=row.display_name or "未设置昵称",
        email_verified=row.email_verified_at is not None) for row in rows[:size]]
    return result_page(items, rows, size)
