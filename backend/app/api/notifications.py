import math
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count_query = select(func.count(Notification.id)).where(Notification.user_id == current_user.id)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    notifications = result.scalars().all()

    items = [
        {
            "id": n.id,
            "user_id": n.user_id,
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "data": n.data,
            "is_read": n.is_read,
            "created_at": n.created_at,
        }
        for n in notifications
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.put("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    nid = uuid.UUID(notification_id)
    result = await db.execute(
        select(Notification).where(
            Notification.id == nid,
            Notification.user_id == current_user.id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    notification.is_read = True
    await db.flush()

    return {"message": "Notification marked as read"}
