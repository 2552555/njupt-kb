import math
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.resource import Resource
from app.models.course import Course
from app.models.major import Major
from app.models.college import College
from app.models.user import User
from app.models.favorite import Favorite
from app.models.download import Download
from app.models.points import PointsLog
from app.models.notification import Notification
from app.schemas.resource import ResourceCreate, ResourceUpdate, ResourceOut
from app.schemas.favorite import FavoriteToggle
from app.schemas.common import PaginatedResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/api/resources", tags=["resources"])


async def _build_resource_item(r: Resource, db: AsyncSession) -> dict:
    """Build resource item with computed fields."""
    # Read all scalar attributes upfront to avoid lazy-load issues
    rid = r.id
    course_id = r.course_id
    uploader_id = r.uploader_id
    item = {
        "id": rid, "course_id": course_id, "uploader_id": uploader_id,
        "title": r.title, "description": r.description, "resource_type": r.resource_type,
        "file_path": r.file_path, "file_name": r.file_name, "file_size": r.file_size,
        "file_type": r.file_type, "page_count": r.page_count,
        "view_count": r.view_count, "download_count": r.download_count,
        "favorite_count": r.favorite_count, "avg_rating": r.avg_rating,
        "rating_count": r.rating_count, "year": r.year, "semester_season": r.semester_season,
        "is_verified": r.is_verified, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    # Uploader name
    u_result = await db.execute(select(User.display_name).where(User.id == uploader_id))
    item["uploader_name"] = u_result.scalar_one_or_none()

    # Course name
    c_result = await db.execute(select(Course.name).where(Course.id == course_id))
    item["course_name"] = c_result.scalar_one_or_none()

    # College name
    col_result = await db.execute(
        select(College.name)
        .join(Major, Major.college_id == College.id)
        .join(Course, Course.major_id == Major.id)
        .where(Course.id == course_id)
    )
    item["college_name"] = col_result.scalar_one_or_none()

    return item


@router.get("")
async def list_resources(
    course_id: Optional[str] = Query(None),
    college_id: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = select(Resource).where(Resource.status != "deleted")
    count_query = select(func.count(Resource.id)).where(Resource.status != "deleted")

    if course_id:
        cid = uuid.UUID(course_id)
        query = query.where(Resource.course_id == cid)
        count_query = count_query.where(Resource.course_id == cid)
    elif college_id:
        cid = uuid.UUID(college_id)
        query = (
            query.join(Course, Resource.course_id == Course.id)
            .join(Major, Course.major_id == Major.id)
            .where(Major.college_id == cid)
        )
        count_query = (
            count_query.join(Course, Resource.course_id == Course.id)
            .join(Major, Course.major_id == Major.id)
            .where(Major.college_id == cid)
        )

    if resource_type:
        query = query.where(Resource.resource_type == resource_type)
        count_query = count_query.where(Resource.resource_type == resource_type)

    if search:
        query = query.where(
            (Resource.title.contains(search)) | (Resource.description.contains(search))
        )
        count_query = count_query.where(
            (Resource.title.contains(search)) | (Resource.description.contains(search))
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Sort
    if sort == "downloads":
        query = query.order_by(Resource.download_count.desc())
    elif sort == "rating":
        query = query.order_by(Resource.avg_rating.desc())
    else:  # newest
        query = query.order_by(Resource.created_at.desc())

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    resources = result.scalars().all()

    items = []
    for r in resources:
        items.append(await _build_resource_item(r, db))

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get("/{resource_id}")
async def get_resource(resource_id: str, db: AsyncSession = Depends(get_db)):
    rid = uuid.UUID(resource_id)
    result = await db.execute(
        select(Resource).where(Resource.id == rid, Resource.status != "deleted")
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    # Increment view count
    resource.view_count += 1
    await db.flush()
    await db.refresh(resource)

    view_count = resource.view_count

    item = await _build_resource_item(resource, db)

    # Tags
    from app.models.tag import ResourceTag, Tag
    tags_result = await db.execute(
        select(Tag.name, Tag.color)
        .join(ResourceTag, Tag.id == ResourceTag.tag_id)
        .where(ResourceTag.resource_id == rid)
    )
    tags = [{"name": t[0], "color": t[1]} for t in tags_result.all()]
    item["tags"] = tags
    item["view_count"] = resource.view_count  # Updated view count

    return item


@router.post("", response_model=ResourceOut, status_code=status.HTTP_201_CREATED)
async def create_resource(
    data: ResourceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify course exists
    course_result = await db.execute(select(Course).where(Course.id == data.course_id))
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    resource = Resource(
        course_id=data.course_id,
        uploader_id=current_user.id,
        title=data.title,
        description=data.description,
        resource_type=data.resource_type,
        file_path=data.file_path,
        file_name=data.file_name,
        file_size=data.file_size,
        file_type=data.file_type,
        page_count=data.page_count,
        year=data.year,
        semester_season=data.semester_season,
        status="active",
    )
    db.add(resource)

    # Award +10 points to uploader
    current_user.points += 10
    points_log = PointsLog(
        user_id=current_user.id,
        amount=10,
        action="upload_resource",
        description=f"上传资源: {data.title}",
    )
    db.add(points_log)

    await db.flush()
    await db.refresh(resource)

    item = await _build_resource_item(resource, db)
    return ResourceOut.model_validate(item)


@router.put("/{resource_id}", response_model=ResourceOut)
async def update_resource(
    resource_id: str,
    data: ResourceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rid = uuid.UUID(resource_id)
    result = await db.execute(select(Resource).where(Resource.id == rid))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if resource.uploader_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(resource, key, value)
    await db.flush()
    await db.refresh(resource)

    item = await _build_resource_item(resource, db)
    return ResourceOut.model_validate(item)


@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rid = uuid.UUID(resource_id)
    result = await db.execute(select(Resource).where(Resource.id == rid))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if resource.uploader_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    resource.status = "deleted"
    await db.flush()
    return {"message": "Resource deleted"}


@router.post("/{resource_id}/download")
async def download_resource(
    resource_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rid = uuid.UUID(resource_id)
    result = await db.execute(select(Resource).where(Resource.id == rid))
    resource = result.scalar_one_or_none()
    if not resource or resource.status == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    # Check if user has enough points
    cost = 2
    if current_user.points < cost:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient points")

    # Deduct from downloader, add to uploader
    current_user.points -= cost
    resource.download_count += 1

    # Get uploader
    uploader_result = await db.execute(select(User).where(User.id == resource.uploader_id))
    uploader = uploader_result.scalar_one_or_none()
    if uploader:
        uploader.points += 1
        uploader_log = PointsLog(
            user_id=uploader.id,
            amount=1,
            action="resource_downloaded",
            resource_id=resource.id,
            description=f"资源被下载: {resource.title}",
        )
        db.add(uploader_log)

    # Record download
    download = Download(user_id=current_user.id, resource_id=resource.id, cost_points=cost)
    db.add(download)

    # Log points
    points_log = PointsLog(
        user_id=current_user.id,
        amount=-cost,
        action="download_resource",
        resource_id=resource.id,
        description=f"下载资源: {resource.title}",
    )
    db.add(points_log)

    await db.flush()
    return {"message": "Download recorded", "points_deducted": cost, "remaining_points": current_user.points}


@router.post("/{resource_id}/favorite")
async def toggle_favorite(
    resource_id: str,
    data: FavoriteToggle,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rid = uuid.UUID(resource_id)
    result = await db.execute(
        select(Favorite).where(
            Favorite.user_id == current_user.id,
            Favorite.resource_id == rid,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        await db.delete(existing)
        # Decrement favorite count
        res_result = await db.execute(select(Resource).where(Resource.id == rid))
        resource = res_result.scalar_one_or_none()
        if resource and resource.favorite_count > 0:
            resource.favorite_count -= 1
        await db.flush()
        return {"message": "Unfavorited", "favorited": False}
    else:
        fav = Favorite(
            user_id=current_user.id,
            resource_id=rid,
            folder_name=data.folder_name or "默认",
        )
        db.add(fav)
        # Increment favorite count
        res_result = await db.execute(select(Resource).where(Resource.id == rid))
        resource = res_result.scalar_one_or_none()
        if resource:
            resource.favorite_count += 1
        await db.flush()
        return {"message": "Favorited", "favorited": True}
