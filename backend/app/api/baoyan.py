import math
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.baoyan import BaoyanPolicy, BaoyanExperience
from app.models.user import User
from app.models.points import PointsLog
from app.schemas.baoyan import BaoyanPolicyOut, BaoyanExperienceCreate, BaoyanExperienceOut
from app.schemas.common import PaginatedResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/api/baoyan", tags=["baoyan"])


@router.get("/policies")
async def list_policies(
    college_id: str = Query(None),
    year: int = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(BaoyanPolicy)
    if college_id:
        cid = uuid.UUID(college_id)
        query = query.where(BaoyanPolicy.college_id == cid)
    if year:
        query = query.where(BaoyanPolicy.year == year)
    query = query.order_by(BaoyanPolicy.year.desc())
    result = await db.execute(query)
    policies = result.scalars().all()
    return [BaoyanPolicyOut.model_validate(p) for p in policies]


@router.get("/experiences")
async def list_experiences(
    college_id: str = Query(None),
    target_university: str = Query(None),
    year: int = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = select(BaoyanExperience).where(BaoyanExperience.status == "active")
    count_query = select(func.count(BaoyanExperience.id)).where(BaoyanExperience.status == "active")

    if college_id:
        cid = uuid.UUID(college_id)
        query = query.where(BaoyanExperience.college_id == cid)
        count_query = count_query.where(BaoyanExperience.college_id == cid)
    if target_university:
        query = query.where(BaoyanExperience.target_university.contains(target_university))
        count_query = count_query.where(BaoyanExperience.target_university.contains(target_university))
    if year:
        query = query.where(BaoyanExperience.year == year)
        count_query = count_query.where(BaoyanExperience.year == year)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(BaoyanExperience.year.desc(), BaoyanExperience.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    experiences = result.scalars().all()

    return PaginatedResponse(
        items=[BaoyanExperienceOut.model_validate(e) for e in experiences],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.post("/experiences", status_code=status.HTTP_201_CREATED)
async def create_experience(
    data: BaoyanExperienceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    experience = BaoyanExperience(
        author_id=current_user.id,
        college_id=data.college_id,
        major_id=data.major_id,
        original_major=data.original_major,
        gpa=data.gpa,
        gpa_rank=data.gpa_rank,
        target_university=data.target_university,
        target_college=data.target_college,
        target_major=data.target_major,
        offer_result=data.offer_result,
        timeline=data.timeline,
        summer_camp_experience=data.summer_camp_experience,
        pre_interview_experience=data.pre_interview_experience,
        interview_questions=data.interview_questions,
        advice=data.advice,
        year=data.year,
        tags=data.tags,
        status="active",
    )
    db.add(experience)

    # Award +15 points
    current_user.points += 15
    points_log = PointsLog(
        user_id=current_user.id,
        amount=15,
        action="share_baoyan_experience",
        description=f"分享保研经验: {data.target_university}",
    )
    db.add(points_log)

    await db.flush()
    await db.refresh(experience)

    return BaoyanExperienceOut.model_validate(experience)


@router.get("/experiences/{experience_id}")
async def get_experience(experience_id: str, db: AsyncSession = Depends(get_db)):
    eid = uuid.UUID(experience_id)
    result = await db.execute(
        select(BaoyanExperience).where(BaoyanExperience.id == eid)
    )
    exp = result.scalar_one_or_none()
    if not exp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experience not found")

    # Increment view count
    exp.view_count += 1
    await db.flush()

    return BaoyanExperienceOut.model_validate(exp)
