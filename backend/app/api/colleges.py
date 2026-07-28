from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.college import College
from app.models.major import Major
from app.models.resource import Resource
from app.models.course import Course

router = APIRouter(prefix="/api/colleges", tags=["colleges"])


@router.get("")
async def list_colleges(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(College).order_by(College.name))
    colleges = result.scalars().all()
    return [{"id": c.id, "name": c.name, "short_name": c.short_name, "website_url": c.website_url, "description": c.description, "created_at": c.created_at} for c in colleges]


@router.get("/{college_id}")
async def get_college(college_id: str, db: AsyncSession = Depends(get_db)):
    from uuid import UUID
    cid = UUID(college_id)
    result = await db.execute(
        select(College).options(selectinload(College.majors)).where(College.id == cid)
    )
    college = result.scalar_one_or_none()
    if not college:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found")

    # Count resources for this college
    res_count_query = (
        select(func.count(Resource.id))
        .join(Course, Resource.course_id == Course.id)
        .join(Major, Course.major_id == Major.id)
        .where(Major.college_id == college.id, Resource.status != "deleted")
    )
    count_result = await db.execute(res_count_query)
    resource_count = count_result.scalar() or 0

    return {
        "id": college.id,
        "name": college.name,
        "short_name": college.short_name,
        "website_url": college.website_url,
        "description": college.description,
        "created_at": college.created_at,
        "majors": [{"id": m.id, "name": m.name, "category": m.category, "degree": m.degree, "duration": m.duration} for m in college.majors],
        "resource_count": resource_count,
    }
