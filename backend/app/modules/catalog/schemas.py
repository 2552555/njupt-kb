"""catalog 对外 DTO 与查询参数（设计文档 §10、§25.1）。

对外一律返回 DTO，不返回 ORM 对象、内部键或风险参数。所有 id 以字符串序列化。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

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
