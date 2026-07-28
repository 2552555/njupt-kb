import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.kaoyan import KaoyanSchool, KaoyanMajor, KaoyanScoreLine
from app.schemas.kaoyan import KaoyanSchoolOut, KaoyanMajorOut, KaoyanScoreLineOut

router = APIRouter(prefix="/api/kaoyan", tags=["kaoyan"])


@router.get("/schools")
async def list_schools(db: AsyncSession = Depends(get_db)):
    query = select(KaoyanSchool).order_by(KaoyanSchool.name)
    result = await db.execute(query)
    schools = result.scalars().all()
    return [KaoyanSchoolOut.model_validate(s) for s in schools]


@router.get("/schools/{school_id}/majors")
async def list_majors(school_id: str, db: AsyncSession = Depends(get_db)):
    sid = uuid.UUID(school_id)
    # Verify school exists
    school_result = await db.execute(select(KaoyanSchool).where(KaoyanSchool.id == sid))
    school = school_result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="School not found")

    query = (
        select(KaoyanMajor)
        .where(KaoyanMajor.school_id == sid)
        .order_by(KaoyanMajor.name)
    )
    result = await db.execute(query)
    majors = result.scalars().all()
    return [KaoyanMajorOut.model_validate(m) for m in majors]


@router.get("/score-lines")
async def list_score_lines(
    school_id: str = Query(None),
    major_id: str = Query(None),
    year: int = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(KaoyanScoreLine)

    if school_id:
        sid = uuid.UUID(school_id)
        query = query.join(KaoyanMajor, KaoyanScoreLine.kaoyan_major_id == KaoyanMajor.id).where(
            KaoyanMajor.school_id == sid
        )
    if major_id:
        mid = uuid.UUID(major_id)
        query = query.where(KaoyanScoreLine.kaoyan_major_id == mid)
    if year:
        query = query.where(KaoyanScoreLine.year == year)

    query = query.order_by(KaoyanScoreLine.year.desc())
    result = await db.execute(query)
    score_lines = result.scalars().all()
    return [KaoyanScoreLineOut.model_validate(sl) for sl in score_lines]
