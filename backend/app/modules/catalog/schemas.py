"""catalog 对外 DTO 与查询参数（设计文档 §10、§25.1）。

对外一律返回 DTO，不返回 ORM 对象、内部键或风险参数。所有 id 以字符串序列化。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class CollegeBrief(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str | None = None
    code: str | None = None


class MajorBrief(BaseModel):
    id: uuid.UUID
    name: str
    code: str | None = None
    category: str | None = None
    college_id: uuid.UUID
    college_name: str


class CollegeDetail(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str | None = None
    code: str | None = None
    majors: list[MajorBrief]


class CourseBrief(BaseModel):
    """docs/INTERFACES.md 约定的课程简要信息。

    `college_name` 取课程归属学院；来源目录未记录归属学院时为 None（不臆造）。
    """

    course_id: uuid.UUID
    code: str | None = None
    name: str
    college_name: str | None = None
    aliases: list[str] = []


class CourseListItem(BaseModel):
    id: uuid.UUID
    code: str | None = None
    name: str
    college_id: uuid.UUID | None = None
    college_name: str | None = None
    aliases: list[str] = []
    curriculum_count: int = 0


class CurriculumLink(BaseModel):
    """课程详情中的“适用目录”：哪些培养方案包含该课程（§11.1）。"""

    curriculum_id: uuid.UUID
    curriculum_name: str
    entry_year: int | None = None
    major_id: uuid.UUID
    major_name: str
    college_id: uuid.UUID
    college_name: str
    requirement_type: str
    recommended_term: str | None = None


class CourseDetail(BaseModel):
    id: uuid.UUID
    code: str | None = None
    name: str
    description: str | None = None
    status: str
    college_id: uuid.UUID | None = None
    college_name: str | None = None
    aliases: list[str] = []
    curricula: list[CurriculumLink] = []
    # 分类资源计数：key 为 resource_type，只统计已发布且对当前查看者可见的资源；
    # 默认空字典保持既有调用方（未传查看者时）兼容。
    resource_counts: dict[str, int] = {}


class TeacherBrief(BaseModel):
    teacher_id: uuid.UUID
    display_name: str
    role: str


class OfferingItem(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    academic_year: str
    season: str
    section_code: str | None = None
    assessment: str | None = None
    textbook: str | None = None
    syllabus: str | None = None
    teachers: list[TeacherBrief] = []


@dataclass(slots=True)
class CourseSearchFilters:
    """课程侧结构化筛选（§19.1）。

    只覆盖 catalog 能负责的维度（学校、学院、专业、培养方案、学期、教师）；
    资料类型与适用年份属资源侧，由 interaction 模块的 /search 在调用本模块后叠加。
    """

    university_id: uuid.UUID | None = None
    college_id: uuid.UUID | None = None
    major_id: uuid.UUID | None = None
    curriculum_id: uuid.UUID | None = None
    semester_id: uuid.UUID | None = None
    teacher_id: uuid.UUID | None = None
    limit: int = 20


# --- 管理员目录维护（§11、§15 CATALOG_MANAGE）-----------------------------------


class CatalogEntityType(StrEnum):
    """管理员可维护的目录实体类型（§11.1）。"""

    UNIVERSITY = "university"
    COLLEGE = "college"
    MAJOR = "major"
    COURSE = "course"
    TEACHER = "teacher"
    SEMESTER = "semester"


class CatalogItemOut(BaseModel):
    """管理员列表项：包含停用/合并项，普通前台接口不返回这些状态。"""

    id: uuid.UUID
    name: str
    status: str
    code: str | None = None
    short_name: str | None = None
    parent_id: uuid.UUID | None = None
    parent_name: str | None = None
    academic_year: str | None = None
    season: str | None = None


class CatalogCreateIn(BaseModel):
    """新建目录项。字段按实体类型取用；service 校验必填项（不臆造父级）。"""

    name: str
    code: str | None = None
    short_name: str | None = None
    slug: str | None = None
    university_id: uuid.UUID | None = None
    college_id: uuid.UUID | None = None
    description: str | None = None
    academic_year: str | None = None
    season: str | None = None


class CatalogRenameIn(BaseModel):
    name: str
    reason: str | None = None


class CatalogDeactivateIn(BaseModel):
    reason: str | None = None


class CatalogMergeIn(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    reason: str


class CatalogReferenceCount(BaseModel):
    """合并影响面：某一引用表/列中被合并项引用的行数。"""

    table: str
    column: str
    label: str
    count: int


class CatalogMergeImpactOut(BaseModel):
    """合并影响面预览（§11.2 规则 7）：执行前必须先给出引用数量。"""

    entity_type: str
    source_id: uuid.UUID
    target_id: uuid.UUID
    source_name: str
    target_name: str
    references: list[CatalogReferenceCount]
    total: int
