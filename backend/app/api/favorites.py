import math
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.favorite import Favorite
from app.models.resource import Resource
from app.models.course import Course
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("")
async def list_favorites(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count_query = select(func.count(Favorite.id)).where(Favorite.user_id == current_user.id)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        select(Favorite)
        .where(Favorite.user_id == current_user.id)
        .order_by(Favorite.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    favorites = result.scalars().all()

    items = []
    for fav in favorites:
        res_result = await db.execute(select(Resource).where(Resource.id == fav.resource_id))
        resource = res_result.scalar_one_or_none()
        course_name = None
        if resource:
            c_result = await db.execute(select(Course.name).where(Course.id == resource.course_id))
            course_name = c_result.scalar_one_or_none()

        items.append({
            "id": fav.id,
            "user_id": fav.user_id,
            "resource_id": fav.resource_id,
            "folder_name": fav.folder_name,
            "created_at": fav.created_at,
            "resource_title": resource.title if resource else None,
            "resource_type": resource.resource_type if resource else None,
            "course_name": course_name,
        })

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )
