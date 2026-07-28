from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.resource import Resource
from app.models.course import Course
from app.models.major import Major
from app.models.college import College
from app.models.user import User
from app.models.baoyan import BaoyanExperience

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def unified_search(
    q: str = Query(..., min_length=1),
    type: str = Query("all"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    results = []

    if type in ("all", "resources"):
        res_query = (
            select(Resource)
            .where(
                Resource.status != "deleted",
                (Resource.title.contains(q)) | (Resource.description.contains(q)),
            )
            .order_by(Resource.created_at.desc())
            .limit(limit)
        )
        res_result = await db.execute(res_query)
        resources = res_result.scalars().all()
        for r in resources:
            # Get course name
            c_result = await db.execute(select(Course.name).where(Course.id == r.course_id))
            course_name = c_result.scalar_one_or_none()
            results.append({
                "type": "resource",
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "resource_type": r.resource_type,
                "course_name": course_name,
                "created_at": r.created_at,
            })

    if type in ("all", "experiences"):
        exp_query = (
            select(BaoyanExperience)
            .where(
                BaoyanExperience.status == "active",
                (BaoyanExperience.target_university.contains(q))
                | (BaoyanExperience.target_major.contains(q))
                | (BaoyanExperience.advice.contains(q)),
            )
            .order_by(BaoyanExperience.created_at.desc())
            .limit(limit)
        )
        exp_result = await db.execute(exp_query)
        experiences = exp_result.scalars().all()
        for e in experiences:
            results.append({
                "type": "baoyan_experience",
                "id": e.id,
                "title": f"保研经验 - {e.target_university} {e.target_major or ''}",
                "description": e.advice[:200] if e.advice else None,
                "target_university": e.target_university,
                "year": e.year,
                "created_at": e.created_at,
            })

    # Sort mixed results by created_at desc
    results.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    results = results[:limit]

    return {"query": q, "type": type, "results": results}
