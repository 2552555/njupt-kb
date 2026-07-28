import math
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.transfer import MajorTransferPolicy, MajorTransferCase
from app.models.user import User
from app.models.points import PointsLog
from app.schemas.transfer import TransferPolicyOut, TransferCaseCreate, TransferCaseOut
from app.schemas.common import PaginatedResponse
from app.core.security import get_current_user

router = APIRouter(prefix="/api/transfer", tags=["transfer"])


@router.get("/policies")
async def list_policies(
    year: int = Query(None),
    college_id: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(MajorTransferPolicy)
    if year:
        query = query.where(MajorTransferPolicy.year == year)
    if college_id:
        cid = uuid.UUID(college_id)
        query = query.where(MajorTransferPolicy.college_id == cid)
    query = query.order_by(MajorTransferPolicy.year.desc())
    result = await db.execute(query)
    policies = result.scalars().all()
    return [TransferPolicyOut.model_validate(p) for p in policies]


@router.get("/cases")
async def list_cases(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    count_query = select(func.count(MajorTransferCase.id))
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        select(MajorTransferCase)
        .order_by(MajorTransferCase.year.desc(), MajorTransferCase.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    cases = result.scalars().all()

    return PaginatedResponse(
        items=[TransferCaseOut.model_validate(c) for c in cases],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.post("/cases", status_code=status.HTTP_201_CREATED)
async def create_case(
    data: TransferCaseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    case = MajorTransferCase(
        author_id=current_user.id,
        year=data.year,
        from_major=data.from_major,
        from_college=data.from_college,
        to_major=data.to_major,
        to_college=data.to_college,
        gpa=data.gpa,
        exam_scores=data.exam_scores,
        experience_text=data.experience_text,
        result=data.result,
    )
    db.add(case)

    # Award +15 points
    current_user.points += 15
    points_log = PointsLog(
        user_id=current_user.id,
        amount=15,
        action="share_transfer_case",
        description=f"分享转专业案例: {data.from_major} -> {data.to_major}",
    )
    db.add(points_log)

    await db.flush()
    await db.refresh(case)

    return TransferCaseOut.model_validate(case)
