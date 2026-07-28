import math
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.points import PointsLog
from app.models.contribution import ContributionDay
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/api/points", tags=["points"])


@router.get("/log")
async def points_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count_query = select(func.count(PointsLog.id)).where(PointsLog.user_id == current_user.id)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        select(PointsLog)
        .where(PointsLog.user_id == current_user.id)
        .order_by(PointsLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    logs = result.scalars().all()

    items = [
        {
            "id": log.id,
            "user_id": log.user_id,
            "amount": log.amount,
            "action": log.action,
            "resource_id": log.resource_id,
            "description": log.description,
            "created_at": log.created_at,
        }
        for log in logs
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.post("/checkin")
async def daily_checkin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()

    # Check if already checked in today
    result = await db.execute(
        select(ContributionDay).where(
            ContributionDay.user_id == current_user.id,
            ContributionDay.date == today,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return {"message": "Already checked in today", "points_earned": 0, "current_points": current_user.points}

    # Calculate streak
    streak = 0
    check_date = today - timedelta(days=1)
    while True:
        result = await db.execute(
            select(ContributionDay).where(
                ContributionDay.user_id == current_user.id,
                ContributionDay.date == check_date,
            )
        )
        day_record = result.scalar_one_or_none()
        if day_record:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    # Base 2 points, +5 bonus every 7 days
    bonus = 5 if (streak + 1) % 7 == 0 else 0
    points_earned = 2 + bonus

    # Create contribution day record
    cd = ContributionDay(
        user_id=current_user.id,
        date=today,
        count=1,
        points_earned=points_earned,
    )
    db.add(cd)

    # Update user points
    current_user.points += points_earned

    # Log points
    log = PointsLog(
        user_id=current_user.id,
        amount=points_earned,
        action="daily_checkin",
        description=f"每日签到 (+{points_earned}，连续{streak + 1}天)",
    )
    db.add(log)

    await db.flush()

    return {
        "message": f"签到成功！连续{streak + 1}天",
        "points_earned": points_earned,
        "current_points": current_user.points,
        "streak": streak + 1,
    }


@router.get("/leaderboard")
async def leaderboard(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(User)
        .where(User.role != "admin")
        .order_by(User.points.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    users = result.scalars().all()

    return [
        {
            "rank": i + 1,
            "user_id": u.id,
            "display_name": u.display_name,
            "points": u.points,
            "avatar_url": u.avatar_url,
        }
        for i, u in enumerate(users)
    ]
