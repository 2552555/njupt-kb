"""课程目录路由：只做参数解析与服务调用（docs/ARCHITECTURE.md §1）。

路径不含 /api/v1，前缀由 app/main.py 统一添加。公开只读接口：不返回草稿或私有文件信息（§19.3）。
资料类型与适用年份属资源侧筛选，由 interaction 的 /search 负责，本模块不实现 /search。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_optional_user, get_request_id, parse_uuid
from app.core.pagination import CursorPage
from app.core.permissions import CATALOG_MANAGE, require
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    CatalogCreateIn,
    CatalogDeactivateIn,
    CatalogEntityType,
    CatalogItemOut,
    CatalogMergeImpactOut,
    CatalogMergeIn,
    CatalogRenameIn,
    CollegeBrief,
    CollegeDetail,
    CourseDetail,
    CourseListItem,
    CourseSearchFilters,
    MajorBrief,
    OfferingItem,
)
from app.modules.ratelimit.deps import rate_limit

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

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
    _rl: None = Depends(rate_limit("browse")),
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
    course_id: str,
    db: AsyncSession = Depends(get_db),
    user: "User | None" = Depends(get_optional_user),
    _rl: None = Depends(rate_limit("browse")),
) -> CourseDetail:
    return await service.get_course_detail(db, parse_uuid(course_id, "course_id"), user=user)


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


# --------------------------------------------------------------------------- #
# 管理员目录维护（§11、§15 CATALOG_MANAGE）。路由只解析参数并调用 service。
# --------------------------------------------------------------------------- #


@router.get("/admin/catalog/{entity_type}", response_model=list[CatalogItemOut])
async def admin_list_catalog(
    entity_type: CatalogEntityType,
    q: str | None = Query(None, description="按名称模糊筛选"),
    status: str | None = Query(None, description="按状态筛选，如 active/inactive/merged"),
    user: "User" = Depends(require(CATALOG_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[CatalogItemOut]:
    return await service.admin_list_catalog(db, entity_type, keyword=q, status=status)


@router.post(
    "/admin/catalog/{entity_type}", response_model=CatalogItemOut, status_code=201
)
async def admin_create_catalog(
    entity_type: CatalogEntityType,
    payload: CatalogCreateIn,
    user: "User" = Depends(require(CATALOG_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CatalogItemOut:
    return await service.admin_create_catalog(
        db, entity_type, payload, actor=user, request_id=request_id
    )


@router.get(
    "/admin/catalog/{entity_type}/merge-impact",
    response_model=CatalogMergeImpactOut,
)
async def admin_catalog_merge_impact(
    entity_type: CatalogEntityType,
    source_id: str = Query(...),
    target_id: str = Query(...),
    user: "User" = Depends(require(CATALOG_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> CatalogMergeImpactOut:
    return await service.admin_merge_impact(
        db,
        entity_type,
        parse_uuid(source_id, "source_id"),
        parse_uuid(target_id, "target_id"),
    )


@router.post("/admin/catalog/{entity_type}/merge", response_model=CatalogMergeImpactOut)
async def admin_merge_catalog(
    entity_type: CatalogEntityType,
    payload: CatalogMergeIn,
    user: "User" = Depends(require(CATALOG_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CatalogMergeImpactOut:
    return await service.admin_merge_catalog(
        db,
        entity_type,
        payload.source_id,
        payload.target_id,
        payload.reason,
        actor=user,
        request_id=request_id,
    )


@router.patch(
    "/admin/catalog/{entity_type}/{item_id}", response_model=CatalogItemOut
)
async def admin_rename_catalog(
    entity_type: CatalogEntityType,
    item_id: uuid.UUID,
    payload: CatalogRenameIn,
    user: "User" = Depends(require(CATALOG_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CatalogItemOut:
    return await service.admin_rename_catalog(
        db, entity_type, item_id, payload, actor=user, request_id=request_id
    )


@router.post(
    "/admin/catalog/{entity_type}/{item_id}/deactivate",
    response_model=CatalogItemOut,
)
async def admin_deactivate_catalog(
    entity_type: CatalogEntityType,
    item_id: uuid.UUID,
    payload: CatalogDeactivateIn,
    user: "User" = Depends(require(CATALOG_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CatalogItemOut:
    return await service.admin_deactivate_catalog(
        db, entity_type, item_id, payload, actor=user, request_id=request_id
    )
