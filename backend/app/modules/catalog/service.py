"""课程目录服务：业务规则的唯一落点（docs/ARCHITECTURE.md §1）。

目录判定规则（设计文档 §11.2）在本模块的体现：
1. 用课程代码 + 学校目录识别课程；检索优先命中代码（`search_courses`）。
2. 同名不同代码默认分开，不按名称合并，列表与检索都不做去重。
4. 不假设代码永久唯一，因此不依赖代码唯一约束，只建索引。
6. 待归类（pending_classification）与已合并（merged）课程不进入正式浏览与检索。
7. 课程改名、换代码与替代关系由 `course_relations` 分别记录，首期不做自动替代决策。

本模块不读取 content 的资源表：资料类型与适用年份属资源侧，由 interaction 的 /search 叠加。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CatalogEntityStatus, CourseStatus
from app.core.errors import InvalidInput, NotFound
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.modules.catalog.models import (
    College,
    Course,
    CourseAlias,
    CourseOffering,
    Curriculum,
    CurriculumCourse,
    Major,
    OfferingTeacher,
    Semester,
    Teacher,
)
from app.modules.catalog.schemas import (
    CollegeBrief,
    CollegeDetail,
    CourseBrief,
    CourseDetail,
    CourseListItem,
    CourseSearchFilters,
    CurriculumLink,
    MajorBrief,
    OfferingItem,
    TeacherBrief,
)

# 公开可浏览的课程状态：待归类与已合并课程不对外出现（§11.2 规则 6）。
_PUBLIC_COURSE_STATUSES = (CourseStatus.ACTIVE.value, CourseStatus.DEPRECATED.value)


def _public_course_condition():
    return Course.status.in_(_PUBLIC_COURSE_STATUSES)


# --- docs/INTERFACES.md 声明的跨模块接口 ---


async def get_course(db: AsyncSession, course_id: uuid.UUID) -> Course:
    """找不到抛 NotFound。返回任意状态，调用方按自身可见性规则决定是否对外暴露。"""
    course = await db.get(Course, course_id)
    if course is None:
        raise NotFound("课程不存在")
    return course


async def get_courses(
    db: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Course]:
    if not ids:
        return {}
    rows = (
        (await db.execute(select(Course).where(Course.id.in_(list(ids)))))
        .scalars()
        .all()
    )
    return {c.id: c for c in rows}


async def get_offering(db: AsyncSession, offering_id: uuid.UUID) -> CourseOffering:
    offering = await db.get(CourseOffering, offering_id)
    if offering is None:
        raise NotFound("开课实例不存在")
    return offering


async def course_brief(db: AsyncSession, course_id: uuid.UUID) -> CourseBrief:
    course = await get_course(db, course_id)
    return (await _to_briefs(db, [course]))[0]


async def search_courses(
    db: AsyncSession,
    query: str | None = None,
    filters: CourseSearchFilters | None = None,
    *,
    q: str | None = None,
    limit: int | None = None,
) -> list[CourseBrief]:
    """课程侧检索能力（§19.1）：课程名/代码/简称/别名匹配 + 结构化筛选。

    由 interaction 的 /search 局部导入调用；只返回公开课程，不泄露草稿或私有信息。

    同时兼容两种调用形式（该函数未在 docs/INTERFACES.md 中声明，调用方实现不一致）：
    - `search_courses(db, query, filters)`（作业要求的形式）
    - `search_courses(db, q=..., limit=...)`（interaction 模块的实际调用）
    """
    keyword = (q if q is not None else query) or ""
    keyword = keyword.strip()
    active_filters = filters or CourseSearchFilters()
    size = normalize_page_size(limit if limit is not None else active_filters.limit)
    stmt: Select = select(Course).where(_public_course_condition())
    stmt = _apply_filters(stmt, active_filters)
    if keyword:
        stmt = _apply_keyword(stmt, keyword)
        stmt = stmt.order_by(_relevance_order(keyword), Course.name, Course.id)
    else:
        stmt = stmt.order_by(Course.name, Course.id)
    courses = list((await db.execute(stmt.limit(size))).scalars().all())
    return await _to_briefs(db, courses)


# --- 浏览接口用到的服务 ---


async def list_courses(
    db: AsyncSession,
    *,
    keyword: str | None,
    filters: CourseSearchFilters,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[CourseListItem]:
    size = normalize_page_size(limit)
    stmt: Select = select(Course).where(_public_course_condition())
    stmt = _apply_filters(stmt, filters)
    if keyword and keyword.strip():
        stmt = _apply_keyword(stmt, keyword.strip())
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_name = str(payload["n"])
            last_id = uuid.UUID(str(payload["i"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput("cursor 无效", details={"reason": "malformed"}) from exc
        stmt = stmt.where(
            or_(
                Course.name > last_name,
                and_(Course.name == last_name, Course.id > last_id),
            )
        )
    # 按名称稳定排序，游标取“名称 + id”保证翻页不重不漏。
    stmt = stmt.order_by(Course.name, Course.id).limit(size + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"n": last.name, "i": str(last.id)})

    course_ids = [c.id for c in items]
    aliases = await _aliases_for(db, course_ids)
    counts = await _curriculum_count_map(db, course_ids)
    names = await _college_name_map(db, [c.owning_college_id for c in items])
    return CursorPage(
        items=[
            CourseListItem(
                id=c.id,
                code=c.code,
                name=c.name,
                college_id=c.owning_college_id,
                college_name=names.get(c.owning_college_id) if c.owning_college_id else None,
                aliases=aliases.get(c.id, []),
                curriculum_count=counts.get(c.id, 0),
            )
            for c in items
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def get_course_detail(db: AsyncSession, course_id: uuid.UUID) -> CourseDetail:
    course = await get_course(db, course_id)
    if course.status not in _PUBLIC_COURSE_STATUSES:
        # 私有/未定状态统一 404，避免泄露存在性（§25.1）。
        raise NotFound("课程不存在")
    names = await _college_name_map(db, [course.owning_college_id])
    aliases = await _aliases_for(db, [course.id])
    rows = (
        await db.execute(
            select(CurriculumCourse, Curriculum, Major, College)
            .join(Curriculum, Curriculum.id == CurriculumCourse.curriculum_id)
            .join(Major, Major.id == Curriculum.major_id)
            .join(College, College.id == Major.college_id)
            .where(CurriculumCourse.course_id == course.id)
            .order_by(College.name, Major.name, Curriculum.name)
        )
    ).all()
    curricula = [
        CurriculumLink(
            curriculum_id=cur.id,
            curriculum_name=cur.name,
            entry_year=cur.entry_year,
            major_id=maj.id,
            major_name=maj.name,
            college_id=col.id,
            college_name=col.name,
            requirement_type=cc.requirement_type,
            recommended_term=cc.recommended_term,
        )
        for cc, cur, maj, col in rows
    ]
    return CourseDetail(
        id=course.id,
        code=course.code,
        name=course.name,
        description=course.description,
        status=course.status,
        college_id=course.owning_college_id,
        college_name=names.get(course.owning_college_id) if course.owning_college_id else None,
        aliases=aliases.get(course.id, []),
        curricula=curricula,
    )


async def list_offerings(db: AsyncSession, course_id: uuid.UUID) -> list[OfferingItem]:
    course = await get_course(db, course_id)
    if course.status not in _PUBLIC_COURSE_STATUSES:
        raise NotFound("课程不存在")
    rows = (
        await db.execute(
            select(CourseOffering, Semester)
            .join(Semester, Semester.id == CourseOffering.semester_id)
            .where(CourseOffering.course_id == course.id)
            .order_by(Semester.academic_year.desc(), Semester.season)
        )
    ).all()
    if not rows:
        return []
    teachers_by_offering = await _offering_teachers(db, [o.id for o, _ in rows])
    return [
        OfferingItem(
            id=o.id,
            course_id=o.course_id,
            academic_year=sem.academic_year,
            season=sem.season,
            section_code=o.section_code,
            assessment=o.assessment,
            textbook=o.textbook,
            syllabus=o.syllabus,
            teachers=teachers_by_offering.get(o.id, []),
        )
        for o, sem in rows
    ]


async def list_colleges(db: AsyncSession) -> list[CollegeBrief]:
    rows = (
        (
            await db.execute(
                select(College)
                .where(College.status == CatalogEntityStatus.ACTIVE.value)
                .order_by(College.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        CollegeBrief(id=c.id, name=c.name, short_name=c.short_name, code=c.code)
        for c in rows
    ]


async def get_college(db: AsyncSession, college_id: uuid.UUID) -> CollegeDetail:
    college = (
        await db.execute(
            select(College).where(
                College.id == college_id, College.status == CatalogEntityStatus.ACTIVE.value
            )
        )
    ).scalar_one_or_none()
    if college is None:
        raise NotFound("学院不存在")
    rows = (
        (
            await db.execute(
                select(Major)
                .where(
                    Major.college_id == college.id,
                    Major.status == CatalogEntityStatus.ACTIVE.value,
                )
                .order_by(Major.name)
            )
        )
        .scalars()
        .all()
    )
    return CollegeDetail(
        id=college.id,
        name=college.name,
        short_name=college.short_name,
        code=college.code,
        majors=[
            MajorBrief(
                id=m.id,
                name=m.name,
                code=m.code,
                category=m.category,
                college_id=college.id,
                college_name=college.name,
            )
            for m in rows
        ],
    )


async def list_majors(
    db: AsyncSession, college_id: uuid.UUID | None = None
) -> list[MajorBrief]:
    stmt = (
        select(Major, College.name)
        .join(College, College.id == Major.college_id)
        .where(Major.status == CatalogEntityStatus.ACTIVE.value)
    )
    if college_id is not None:
        stmt = stmt.where(Major.college_id == college_id)
    stmt = stmt.order_by(College.name, Major.name)
    rows = (await db.execute(stmt)).all()
    return [
        MajorBrief(
            id=m.id,
            name=m.name,
            code=m.code,
            category=m.category,
            college_id=m.college_id,
            college_name=college_name,
        )
        for m, college_name in rows
    ]


# --- 内部工具 ---


def _apply_filters(stmt: Select, filters: CourseSearchFilters) -> Select:
    """结构化筛选：学院/专业/培养方案经 curriculum_courses 关联，学期/教师经开课实例关联。"""
    if filters.university_id is not None:
        stmt = stmt.where(Course.university_id == filters.university_id)
    if filters.college_id is not None:
        sub = (
            select(CurriculumCourse.course_id)
            .join(Curriculum, Curriculum.id == CurriculumCourse.curriculum_id)
            .join(Major, Major.id == Curriculum.major_id)
            .where(Major.college_id == filters.college_id)
        )
        stmt = stmt.where(Course.id.in_(sub))
    if filters.major_id is not None:
        sub = (
            select(CurriculumCourse.course_id)
            .join(Curriculum, Curriculum.id == CurriculumCourse.curriculum_id)
            .where(Curriculum.major_id == filters.major_id)
        )
        stmt = stmt.where(Course.id.in_(sub))
    if filters.curriculum_id is not None:
        sub = select(CurriculumCourse.course_id).where(
            CurriculumCourse.curriculum_id == filters.curriculum_id
        )
        stmt = stmt.where(Course.id.in_(sub))
    if filters.semester_id is not None:
        sub = select(CourseOffering.course_id).where(
            CourseOffering.semester_id == filters.semester_id
        )
        stmt = stmt.where(Course.id.in_(sub))
    if filters.teacher_id is not None:
        sub = (
            select(CourseOffering.course_id)
            .join(OfferingTeacher, OfferingTeacher.offering_id == CourseOffering.id)
            .where(OfferingTeacher.teacher_id == filters.teacher_id)
        )
        stmt = stmt.where(Course.id.in_(sub))
    return stmt


def _apply_keyword(stmt: Select, keyword: str) -> Select:
    """课程名、代码、别名任一命中即可（§19.1）。"""
    pattern = f"%{keyword}%"
    alias_sub = select(CourseAlias.course_id).where(CourseAlias.alias.ilike(pattern))
    return stmt.where(
        or_(
            Course.name.ilike(pattern),
            Course.code.ilike(pattern),
            Course.id.in_(alias_sub),
        )
    )


def _relevance_order(keyword: str):
    """相关性：代码/名称精确 > 前缀 > 其他，避免同分时结果随机（§19.1）。"""
    return case(
        (Course.code == keyword, 0),
        (Course.name == keyword, 1),
        (Course.code.ilike(f"{keyword}%"), 2),
        (Course.name.ilike(f"{keyword}%"), 3),
        else_=4,
    )


async def _to_briefs(db: AsyncSession, courses: list[Course]) -> list[CourseBrief]:
    if not courses:
        return []
    aliases = await _aliases_for(db, [c.id for c in courses])
    names = await _college_name_map(db, [c.owning_college_id for c in courses])
    return [
        CourseBrief(
            course_id=c.id,
            code=c.code,
            name=c.name,
            college_name=names.get(c.owning_college_id) if c.owning_college_id else None,
            aliases=aliases.get(c.id, []),
        )
        for c in courses
    ]


async def _aliases_for(
    db: AsyncSession, course_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not course_ids:
        return {}
    rows = (
        await db.execute(
            select(CourseAlias.course_id, CourseAlias.alias)
            .where(CourseAlias.course_id.in_(list(course_ids)))
            .order_by(CourseAlias.alias)
        )
    ).all()
    out: dict[uuid.UUID, list[str]] = {}
    for course_id, alias in rows:
        out.setdefault(course_id, []).append(alias)
    return out


async def _college_name_map(
    db: AsyncSession, college_ids: Sequence[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    wanted = {cid for cid in college_ids if cid is not None}
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(College.id, College.name).where(College.id.in_(list(wanted)))
        )
    ).all()
    return {cid: name for cid, name in rows}


async def _curriculum_count_map(
    db: AsyncSession, course_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not course_ids:
        return {}
    rows = (
        await db.execute(
            select(CurriculumCourse.course_id, func.count())
            .where(CurriculumCourse.course_id.in_(list(course_ids)))
            .group_by(CurriculumCourse.course_id)
        )
    ).all()
    return {course_id: int(count) for course_id, count in rows}


async def _offering_teachers(
    db: AsyncSession, offering_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[TeacherBrief]]:
    if not offering_ids:
        return {}
    rows = (
        await db.execute(
            select(
                OfferingTeacher.offering_id,
                Teacher.id,
                Teacher.display_name,
                OfferingTeacher.role,
            )
            .join(Teacher, Teacher.id == OfferingTeacher.teacher_id)
            .where(OfferingTeacher.offering_id.in_(list(offering_ids)))
            .order_by(OfferingTeacher.role, Teacher.display_name)
        )
    ).all()
    out: dict[uuid.UUID, list[TeacherBrief]] = {}
    for offering_id, teacher_id, display_name, role in rows:
        out.setdefault(offering_id, []).append(
            TeacherBrief(teacher_id=teacher_id, display_name=display_name, role=role)
        )
    return out
