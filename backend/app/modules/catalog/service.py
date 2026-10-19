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
from typing import NamedTuple


from sqlalchemy import Select, String, and_, case, column, delete, func, or_, select, table, tuple_, update
from sqlalchemy import Uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.enums import CatalogEntityStatus, CourseStatus, PublicationStatus, SemesterSeason
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
    University,
)
from app.modules.catalog.schemas import (
    CatalogCreateIn,
    CatalogDeactivateIn,
    CatalogEntityType,
    CatalogItemOut,
    CatalogMergeImpactOut,
    CatalogReferenceCount,
    CatalogRenameIn,
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

# 只读引用 resources 表做分类计数：不导入 content 模型类（§2.2）。
_RESOURCES = table(
    "resources",
    column("id", Uuid),
    column("course_id", Uuid),
    column("resource_type", String),
    column("publication_status", String),
)


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


async def get_course_detail(
    db: AsyncSession, course_id: uuid.UUID, user=None
) -> CourseDetail:
    """课程详情。`user` 为当前查看者（可为 None）：分类计数按其对资源的可见范围统计。"""
    course = await get_course(db, course_id)
    if course.status not in _PUBLIC_COURSE_STATUSES:
        # 私有/未定状态统一 404，避免泄露存在性（§25.1）。
        raise NotFound("课程不存在")
    names = await _college_name_map(db, [course.owning_college_id])
    aliases = await _aliases_for(db, [course.id])
    resource_counts = await _resource_type_counts(db, course.id, user)
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
        resource_counts=resource_counts,
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


async def _resource_type_counts(
    db: AsyncSession, course_id: uuid.UUID, user
) -> dict[str, int]:
    """课程下按 resource_type 分组计数（一次 GROUP BY 查询）。

    只统计已发布且对当前查看者可见的资源：可见集合由 access 的可见性谓词决定，
    禁止裸 COUNT，避免私有资源的存在性被计数泄露（§25.1）。
    """
    from app.modules.access.visibility import visible_resource_ids

    stmt = (
        select(_RESOURCES.c.resource_type, func.count())
        .select_from(_RESOURCES)
        .where(
            _RESOURCES.c.course_id == course_id,
            _RESOURCES.c.publication_status == PublicationStatus.PUBLISHED.value,
            _RESOURCES.c.id.in_(visible_resource_ids(user)),
        )
        .group_by(_RESOURCES.c.resource_type)
    )
    rows = (await db.execute(stmt)).all()
    return {resource_type: int(count) for resource_type, count in rows}


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


async def classification_catalog(db: AsyncSession) -> list[dict]:
    """Active course identities and aliases for filename suggestions; never merge names."""
    courses = list((await db.execute(select(Course).where(Course.status == CourseStatus.ACTIVE.value))).scalars())
    aliases: dict[uuid.UUID, list[str]] = {}
    for alias in (await db.execute(select(CourseAlias))).scalars():
        aliases.setdefault(alias.course_id, []).append(alias.alias)
    return [{"id": str(c.id), "name": c.name, "code": c.code,
             "aliases": aliases.get(c.id, [])} for c in courses]


# --------------------------------------------------------------------------- #
# 管理员目录维护（§11、§15 CATALOG_MANAGE）
#
# 本节集中管理员对 大学/学院/专业/课程/教师/学期 的创建、改名、停用与合并规则；
# router 只解析参数。
# - 停用是软停用：置 status 为 inactive，不物理删除；已关联资料仍按既有权限读取
#   （§11.2 规则 6：状态只影响对外选择与检索，不连带撤下资料）。
# - 合并先给出影响面数量再执行；引用其它模块的表只按表名/列名批量操作，不导入对方
#   模型类（docs/ARCHITECTURE.md §2.2）。归属类字段不是审核/发布状态列，不越权改写
#   对方的业务状态；单条资源的归属更正仍走 content 的服务函数。
# --------------------------------------------------------------------------- #


class _EntitySpec(NamedTuple):
    model: type
    name_attr: str  # 展示与改名使用的字段
    status_attr: str
    references: tuple[tuple[str, str, str], ...]  # (表名, 列名, 中文标签)
    parent_attr: str | None
    parent_model: type | None
    dedupe: dict[str, tuple[str, ...]]  # 表名 -> 合并前需去重的其它唯一列


# 合并前需去重的关联表：其它唯一列用于识别会撞唯一约束的重复行（§11.2 规则 7）。
_COURSE_MERGE_REFERENCES = (
    ("resources", "course_id", "资料"),
    ("curriculum_courses", "course_id", "培养方案课程"),
    ("course_offerings", "course_id", "开课实例"),
    ("course_aliases", "course_id", "课程别名"),
    ("corrections", "suggested_course_id", "纠错建议课程"),
    ("resource_requests", "course_id", "求资料需求"),
)

_SPECS: dict[CatalogEntityType, _EntitySpec] = {
    CatalogEntityType.UNIVERSITY: _EntitySpec(
        model=University,
        name_attr="name",
        status_attr="status",
        references=(
            ("colleges", "university_id", "学院"),
            ("majors", "university_id", "专业"),
            ("courses", "university_id", "课程"),
            ("semesters", "university_id", "学期"),
            ("teachers", "university_id", "教师"),
        ),
        parent_attr=None,
        parent_model=None,
        dedupe={"semesters": ("academic_year", "season")},
    ),
    CatalogEntityType.COLLEGE: _EntitySpec(
        model=College,
        name_attr="name",
        status_attr="status",
        references=(
            ("majors", "college_id", "专业"),
            ("courses", "owning_college_id", "归属课程"),
        ),
        parent_attr="university_id",
        parent_model=University,
        dedupe={},
    ),
    CatalogEntityType.MAJOR: _EntitySpec(
        model=Major,
        name_attr="name",
        status_attr="status",
        references=(("curricula", "major_id", "培养方案"),),
        parent_attr="college_id",
        parent_model=College,
        dedupe={},
    ),
    CatalogEntityType.COURSE: _EntitySpec(
        model=Course,
        name_attr="name",
        status_attr="status",
        references=_COURSE_MERGE_REFERENCES,
        parent_attr="owning_college_id",
        parent_model=College,
        dedupe={
            "curriculum_courses": ("curriculum_id",),
            "course_aliases": ("alias",),
        },
    ),
    CatalogEntityType.TEACHER: _EntitySpec(
        model=Teacher,
        name_attr="display_name",
        status_attr="status",
        references=(("offering_teachers", "teacher_id", "开课教师"),),
        parent_attr="university_id",
        parent_model=University,
        dedupe={"offering_teachers": ("offering_id", "role")},
    ),
    CatalogEntityType.SEMESTER: _EntitySpec(
        model=Semester,
        name_attr="academic_year",
        status_attr="status",
        references=(("course_offerings", "semester_id", "开课实例"),),
        parent_attr="university_id",
        parent_model=University,
        dedupe={},
    ),
}


def _spec(entity_type: CatalogEntityType) -> _EntitySpec:
    try:
        return _SPECS[CatalogEntityType(entity_type)]
    except ValueError as exc:
        raise InvalidInput("不支持的目录类型") from exc


def _inactive_status(spec: _EntitySpec) -> str:
    if spec.model is Course:
        return CourseStatus.INACTIVE.value
    return CatalogEntityStatus.INACTIVE.value


def _merged_status(spec: _EntitySpec) -> str:
    if spec.model is Course:
        return CourseStatus.MERGED.value
    return CatalogEntityStatus.MERGED.value


async def _get_item(db: AsyncSession, spec: _EntitySpec, item_id: uuid.UUID):
    item = await db.get(spec.model, item_id)
    if item is None:
        raise NotFound("目录项不存在")
    return item


def _item_out(
    spec: _EntitySpec, row, parent_names: dict[uuid.UUID, str]
) -> CatalogItemOut:
    parent_id = getattr(row, spec.parent_attr) if spec.parent_attr else None
    return CatalogItemOut(
        id=row.id,
        name=getattr(row, spec.name_attr),
        status=getattr(row, spec.status_attr),
        code=getattr(row, "code", None),
        short_name=getattr(row, "short_name", None),
        parent_id=parent_id,
        parent_name=parent_names.get(parent_id) if parent_id else None,
        academic_year=getattr(row, "academic_year", None),
        season=getattr(row, "season", None),
    )


async def _parent_name_map(
    db: AsyncSession, spec: _EntitySpec, rows: list
) -> dict[uuid.UUID, str]:
    if spec.parent_attr is None or spec.parent_model is None:
        return {}
    ids = {
        getattr(row, spec.parent_attr)
        for row in rows
        if getattr(row, spec.parent_attr) is not None
    }
    if not ids:
        return {}
    result = await db.execute(
        select(spec.parent_model.id, spec.parent_model.name).where(
            spec.parent_model.id.in_(ids)
        )
    )
    return {parent_id: name for parent_id, name in result.all()}


async def admin_list_catalog(
    db: AsyncSession,
    entity_type: CatalogEntityType,
    *,
    keyword: str | None = None,
    status: str | None = None,
) -> list[CatalogItemOut]:
    """管理员列表：包含停用/合并项，前台接口不返回这些状态（§11.2 规则 6）。"""
    spec = _spec(entity_type)
    stmt = select(spec.model)
    if status:
        stmt = stmt.where(getattr(spec.model, spec.status_attr) == status)
    if keyword and keyword.strip():
        stmt = stmt.where(
            getattr(spec.model, spec.name_attr).ilike(f"%{keyword.strip()}%")
        )
    stmt = stmt.order_by(getattr(spec.model, spec.name_attr), spec.model.id)
    rows = list((await db.execute(stmt)).scalars().all())
    parent_names = await _parent_name_map(db, spec, rows)
    return [_item_out(spec, row, parent_names) for row in rows]


async def admin_create_catalog(
    db: AsyncSession,
    entity_type: CatalogEntityType,
    payload: CatalogCreateIn,
    *,
    actor,
    request_id: str,
) -> CatalogItemOut:
    """新建目录项。父级必须真实存在，不臆造层级（§11.1）。"""
    spec = _spec(entity_type)
    name = (payload.name or "").strip()
    if not name:
        raise InvalidInput("名称不能为空")
    values: dict = {spec.name_attr: name}
    if hasattr(spec.model, "code"):
        values["code"] = (payload.code or "").strip() or None

    if entity_type == CatalogEntityType.UNIVERSITY:
        slug = (payload.slug or "").strip()
        if not slug:
            raise InvalidInput("学校 slug 不能为空")
        values["slug"] = slug
    elif entity_type == CatalogEntityType.COLLEGE:
        if payload.university_id is None:
            raise InvalidInput("学院必须指定学校")
        await _require_parent(db, University, payload.university_id, "学校不存在")
        values["university_id"] = payload.university_id
        values["short_name"] = (payload.short_name or "").strip() or None
    elif entity_type == CatalogEntityType.MAJOR:
        if payload.college_id is None:
            raise InvalidInput("专业必须指定学院")
        college = await _require_parent(db, College, payload.college_id, "学院不存在")
        values["college_id"] = payload.college_id
        values["university_id"] = college.university_id
    elif entity_type == CatalogEntityType.COURSE:
        if payload.university_id is None:
            raise InvalidInput("课程必须指定学校")
        await _require_parent(db, University, payload.university_id, "学校不存在")
        values["university_id"] = payload.university_id
        if payload.college_id is not None:
            await _require_parent(db, College, payload.college_id, "学院不存在")
            values["owning_college_id"] = payload.college_id
        values["description"] = (payload.description or "").strip() or None
    elif entity_type == CatalogEntityType.TEACHER:
        if payload.university_id is None:
            raise InvalidInput("教师必须指定学校")
        await _require_parent(db, University, payload.university_id, "学校不存在")
        values["university_id"] = payload.university_id
    elif entity_type == CatalogEntityType.SEMESTER:
        if payload.university_id is None:
            raise InvalidInput("学期必须指定学校")
        await _require_parent(db, University, payload.university_id, "学校不存在")
        season = (payload.season or "").strip()
        if season not in tuple(m.value for m in SemesterSeason):
            raise InvalidInput("学期季节无效")
        values["university_id"] = payload.university_id
        values["season"] = season

    item = spec.model(**values)
    db.add(item)
    await db.flush()
    await record_audit(
        db,
        action="catalog.create",
        object_type=entity_type.value,
        object_id=item.id,
        actor_id=actor.id,
        after={"name": name, "status": getattr(item, spec.status_attr)},
        reason=None,
        request_id=request_id,
    )
    return _item_out(spec, item, {})


async def _require_parent(db: AsyncSession, model: type, parent_id: uuid.UUID, message: str):
    parent = await db.get(model, parent_id)
    if parent is None:
        raise InvalidInput(message)
    return parent


async def admin_rename_catalog(
    db: AsyncSession,
    entity_type: CatalogEntityType,
    item_id: uuid.UUID,
    payload: CatalogRenameIn,
    *,
    actor,
    request_id: str,
) -> CatalogItemOut:
    """改名保留历史：只改当前名称，不改已关联资料（§11.1）。"""
    spec = _spec(entity_type)
    item = await _get_item(db, spec, item_id)
    name = (payload.name or "").strip()
    if not name:
        raise InvalidInput("名称不能为空")
    before = getattr(item, spec.name_attr)
    setattr(item, spec.name_attr, name)
    await db.flush()
    await record_audit(
        db,
        action="catalog.rename",
        object_type=entity_type.value,
        object_id=item_id,
        actor_id=actor.id,
        before={"name": before},
        after={"name": name},
        reason=payload.reason,
        request_id=request_id,
    )
    parent_names = await _parent_name_map(db, spec, [item])
    return _item_out(spec, item, parent_names)


async def admin_deactivate_catalog(
    db: AsyncSession,
    entity_type: CatalogEntityType,
    item_id: uuid.UUID,
    payload: CatalogDeactivateIn,
    *,
    actor,
    request_id: str,
) -> CatalogItemOut:
    """软停用：不出现在前台选择器与检索，已关联资料仍可读（§11.2 规则 6）。"""
    spec = _spec(entity_type)
    item = await _get_item(db, spec, item_id)
    before = getattr(item, spec.status_attr)
    setattr(item, spec.status_attr, _inactive_status(spec))
    await db.flush()
    await record_audit(
        db,
        action="catalog.deactivate",
        object_type=entity_type.value,
        object_id=item_id,
        actor_id=actor.id,
        before={"status": before},
        after={"status": _inactive_status(spec)},
        reason=payload.reason,
        request_id=request_id,
    )
    parent_names = await _parent_name_map(db, spec, [item])
    return _item_out(spec, item, parent_names)


async def _count_reference(
    db: AsyncSession, table_name: str, column_name: str, value: uuid.UUID
) -> int:
    t = table(table_name, column(column_name, Uuid))
    stmt = select(func.count()).select_from(t).where(t.c[column_name] == value)
    return int(await db.scalar(stmt) or 0)


async def _reassign_reference(
    db: AsyncSession, table_name: str, column_name: str, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    t = table(table_name, column(column_name, Uuid))
    await db.execute(
        update(t).where(t.c[column_name] == source_id).values({column_name: target_id})
    )


async def _drop_conflicting_reference(
    db: AsyncSession,
    table_name: str,
    column_name: str,
    other_columns: tuple[str, ...],
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> None:
    """删除合并后与目标重复、会撞唯一约束的行（只删重复，不删唯一内容）。"""
    t = table(
        table_name,
        column(column_name, Uuid),
        *(column(name) for name in other_columns),
    )
    others = [t.c[name] for name in other_columns]
    sub = select(*others).where(t.c[column_name] == target_id)
    stmt = delete(t).where(t.c[column_name] == source_id)
    if len(others) == 1:
        stmt = stmt.where(others[0].in_(sub))
    else:
        stmt = stmt.where(tuple_(*others).in_(sub))
    await db.execute(stmt)


async def admin_merge_impact(
    db: AsyncSession,
    entity_type: CatalogEntityType,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> CatalogMergeImpactOut:
    """合并影响面预览：执行前先给出各引用表被归并的行数（§11.2 规则 7）。"""
    spec = _spec(entity_type)
    if source_id == target_id:
        raise InvalidInput("合并的源与目标不能相同")
    source = await _get_item(db, spec, source_id)
    target = await _get_item(db, spec, target_id)
    references: list[CatalogReferenceCount] = []
    total = 0
    for table_name, column_name, label in spec.references:
        count = await _count_reference(db, table_name, column_name, source_id)
        references.append(
            CatalogReferenceCount(
                table=table_name, column=column_name, label=label, count=count
            )
        )
        total += count
    return CatalogMergeImpactOut(
        entity_type=entity_type.value,
        source_id=source_id,
        target_id=target_id,
        source_name=getattr(source, spec.name_attr),
        target_name=getattr(target, spec.name_attr),
        references=references,
        total=total,
    )


async def admin_merge_catalog(
    db: AsyncSession,
    entity_type: CatalogEntityType,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    reason: str,
    *,
    actor,
    request_id: str,
) -> CatalogMergeImpactOut:
    """把源项归并到目标项：批量改写引用，源项标记为 merged（不物理删除）。"""
    spec = _spec(entity_type)
    reason = (reason or "").strip()
    if not reason:
        raise InvalidInput("合并必须填写理由")
    impact = await admin_merge_impact(db, entity_type, source_id, target_id)
    for table_name, column_name, _ in spec.references:
        other_columns = spec.dedupe.get(table_name)
        if other_columns:
            await _drop_conflicting_reference(
                db, table_name, column_name, other_columns, source_id, target_id
            )
        await _reassign_reference(db, table_name, column_name, source_id, target_id)

    source = await _get_item(db, spec, source_id)
    before = {
        "name": getattr(source, spec.name_attr),
        "status": getattr(source, spec.status_attr),
    }
    setattr(source, spec.status_attr, _merged_status(spec))
    await db.flush()
    await record_audit(
        db,
        action="catalog.merge",
        object_type=entity_type.value,
        object_id=source_id,
        actor_id=actor.id,
        before=before,
        after={
            "name": getattr(source, spec.name_attr),
            "status": _merged_status(spec),
            "target_id": str(target_id),
            "affected": impact.total,
        },
        reason=reason,
        request_id=request_id,
    )
    return impact
