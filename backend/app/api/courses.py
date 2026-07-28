import math
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.course import Course
from app.models.major import Major
from app.models.resource import Resource
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.get("")
async def list_courses(
    college_id: str = Query(None),
    major_id: str = Query(None),
    search: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = select(Course)
    count_query = select(func.count(Course.id))

    if major_id:
        mid = uuid.UUID(major_id)
        query = query.where(Course.major_id == mid)
        count_query = count_query.where(Course.major_id == mid)
    elif college_id:
        cid = uuid.UUID(college_id)
        query = query.join(Major, Course.major_id == Major.id).where(Major.college_id == cid)
        count_query = count_query.join(Major, Course.major_id == Major.id).where(Major.college_id == cid)

    if search:
        query = query.where(Course.name.contains(search))
        count_query = count_query.where(Course.name.contains(search))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size).order_by(Course.name)
    result = await db.execute(query)
    courses = result.scalars().all()

    return PaginatedResponse(
        items=[{"id": c.id, "major_id": c.major_id, "name": c.name, "course_code": c.course_code, "semester": c.semester, "type": c.type, "credits": c.credits, "description": c.description, "teacher": c.teacher, "created_at": c.created_at} for c in courses],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get("/{course_id}")
async def get_course(course_id: str, db: AsyncSession = Depends(get_db)):
    cid = uuid.UUID(course_id)
    result = await db.execute(
        select(Course).options(selectinload(Course.resources)).where(Course.id == cid)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    # Count resources by type
    resource_types = {}
    for r in course.resources:
        if r.status != "deleted":
            resource_types[r.resource_type] = resource_types.get(r.resource_type, 0) + 1

    return {
        "id": course.id,
        "major_id": course.major_id,
        "name": course.name,
        "course_code": course.course_code,
        "semester": course.semester,
        "type": course.type,
        "credits": course.credits,
        "description": course.description,
        "teacher": course.teacher,
        "created_at": course.created_at,
        "resource_count_by_type": resource_types,
        "total_resources": sum(resource_types.values()),
    }
