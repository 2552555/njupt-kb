import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.comment import Comment
from app.models.resource import Resource
from app.models.user import User
from app.models.points import PointsLog
from app.schemas.comment import CommentCreate, CommentOut
from app.core.security import get_current_user

router = APIRouter(prefix="/api/resources", tags=["comments"])


@router.get("/{resource_id}/comments")
async def list_comments(
    resource_id: str,
    db: AsyncSession = Depends(get_db),
):
    rid = uuid.UUID(resource_id)
    # Verify resource exists
    res_result = await db.execute(select(Resource).where(Resource.id == rid))
    resource = res_result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    result = await db.execute(
        select(Comment).where(Comment.resource_id == rid, Comment.parent_id == None)
        .order_by(Comment.created_at.desc())
    )
    comments = result.scalars().all()

    items = []
    for c in comments:
        user_result = await db.execute(select(User).where(User.id == c.user_id))
        user = user_result.scalar_one_or_none()
        items.append({
            "id": c.id,
            "resource_id": c.resource_id,
            "user_id": c.user_id,
            "content": c.content,
            "rating": c.rating,
            "parent_id": c.parent_id,
            "created_at": c.created_at,
            "user_display_name": user.display_name if user else None,
            "user_avatar_url": user.avatar_url if user else None,
        })

    return items


@router.post("/{resource_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    resource_id: str,
    data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rid = uuid.UUID(resource_id)
    # Verify resource exists
    res_result = await db.execute(select(Resource).where(Resource.id == rid))
    resource = res_result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    comment = Comment(
        resource_id=rid,
        user_id=current_user.id,
        content=data.content,
        rating=data.rating,
    )
    db.add(comment)

    # Award +1 point
    current_user.points += 1
    points_log = PointsLog(
        user_id=current_user.id,
        amount=1,
        action="comment",
        resource_id=resource.id,
        description=f"评论资源: {resource.title}",
    )
    db.add(points_log)

    # Update resource avg_rating
    if data.rating is not None:
        new_count = resource.rating_count + 1
        new_avg = ((resource.avg_rating * resource.rating_count) + data.rating) / new_count
        resource.avg_rating = round(new_avg, 2)
        resource.rating_count = new_count

    await db.flush()
    await db.refresh(comment)

    return {
        "id": comment.id,
        "resource_id": comment.resource_id,
        "user_id": comment.user_id,
        "content": comment.content,
        "rating": comment.rating,
        "parent_id": comment.parent_id,
        "created_at": comment.created_at,
        "user_display_name": current_user.display_name,
        "user_avatar_url": current_user.avatar_url,
    }
