"""课程目录路由：只做参数解析与服务调用（docs/ARCHITECTURE.md §1）。

路径不含 /api/v1，前缀由 app/main.py 统一添加。公开只读接口：不返回草稿或私有文件信息（§19.3）。
资料类型与适用年份属资源侧筛选，由 interaction 的 /search 负责，本模块不实现 /search。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, parse_uuid
from app.core.pagination import CursorPage
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    CollegeBrief,
    CollegeDetail,
    CourseDetail,
    CourseListItem,
    CourseSearchFilters,
    MajorBrief,
    OfferingItem,
)

router = APIRouter(tags=["catalog"])


def _optional_uuid(value: str | None, field: str) -> uuid.UUID | None:
    return parse_uuid(value, field) if value else None


@router.get("/courses", response_model=CursorPage[CourseListItem])
async def list_courses(
    q: str | None = Query(None, description="课程名、课程代码或别名关键词"),
    university_id: str | None = Query(None),
    college_id: str | None = Query(None),
    major_id: str | None = Query(None),
    curriculum_id: str | None = Query(None),
    semester_id: str | None = Query(None),
    teacher_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int | None = Query(None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CourseListItem]:
    filters = CourseSearchFilters(
        university_id=_optional_uuid(university_id, "university_id"),
        college_id=_optional_uuid(college_id, "college_id"),
        major_id=_optional_uuid(major_id, "major_id"),
        curriculum_id=_optional_uuid(curriculum_id, "curriculum_id"),
        semester_id=_optional_uuid(semester_id, "semester_id"),
        teacher_id=_optional_uuid(teacher_id, "teacher_id"),
    )
    return await service.list_courses(
        db, keyword=q, filters=filters, limit=limit, cursor=cursor
    )


@router.get("/courses/{course_id}", response_model=CourseDetail)
async def get_course(
    course_id: str, db: AsyncSession = Depends(get_db)
) -> CourseDetail:
    return await service.get_course_detail(db, parse_uuid(course_id, "course_id"))


@router.get("/courses/{course_id}/offerings", response_model=list[OfferingItem])
async def list_course_offerings(
    course_id: str, db: AsyncSession = Depends(get_db)
) -> list[OfferingItem]:
    return await service.list_offerings(db, parse_uuid(course_id, "course_id"))


@router.get("/colleges", response_model=list[CollegeBrief])
async def list_colleges(db: AsyncSession = Depends(get_db)) -> list[CollegeBrief]:
    return await service.list_colleges(db)


@router.get("/colleges/{college_id}", response_model=CollegeDetail)
async def get_college(
    college_id: str, db: AsyncSession = Depends(get_db)
) -> CollegeDetail:
    return await service.get_college(db, parse_uuid(college_id, "college_id"))


@router.get("/majors", response_model=list[MajorBrief])
async def list_majors(
    college_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[MajorBrief]:
    return await service.list_majors(db, _optional_uuid(college_id, "college_id"))
