"""课程目录模型（设计文档 §11.1）。

要点：
- `Course` 表达课程身份（学校 + 归属学院 + 课程代码 + 名称），同名不同代码默认分开，不按名称合并（§11.2）。
- 课程与专业的关联走 `curriculum_courses`（培养方案 × 课程），因为目录不是严格单父树，
  公共课与跨专业选课都要能表达（§11.1）。
- `CourseOffering` 是“一次实际开课”，教师走 `offering_teachers`，不把教师固定成课程上的字符串。
- 教材章节、考试年份、适用学期分别表达，不共用一个模糊的 `year` 字段。
- 跨模块外键只引用字符串表名，不导入其他模块的模型类（docs/ARCHITECTURE.md §2.2）。
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    CatalogEntityStatus,
    CourseStatus,
    RelationType,
    RequirementType,
    SemesterSeason,
    TeacherRole,
)
from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "University",
    "College",
    "Major",
    "Curriculum",
    "Course",
    "CurriculumCourse",
    "Semester",
    "Teacher",
    "CourseOffering",
    "OfferingTeacher",
    "CourseAlias",
    "CourseRelation",
]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """状态列 CheckConstraint 的 SQL：取值以 app.core.enums 为唯一来源（§4）。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class University(UUIDPrimaryKey, Timestamped, Base):
    """首期只运营一所学校，但保留实体以免把学校写死在代码里（§11.1）。"""

    __tablename__ = "universities"
    __table_args__ = (
        CheckConstraint(_in("status", CatalogEntityStatus), name="status_valid"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CatalogEntityStatus.ACTIVE.value
    )


class College(UUIDPrimaryKey, Timestamped, Base):
    """学院更名保留历史，不直接覆盖所有历史展示（§11.1）。"""

    __tablename__ = "colleges"
    __table_args__ = (
        Index("ix_colleges_university_name", "university_id", "name"),
        CheckConstraint(_in("status", CatalogEntityStatus), name="status_valid"),
    )

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 简称（如“计算机学院”）用于界面与检索；代码在真实目录确认前留空。
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CatalogEntityStatus.ACTIVE.value
    )


class Major(UUIDPrimaryKey, Timestamped, Base):
    """专业不使用专业名称作为关联键（§11.1）。"""

    __tablename__ = "majors"
    __table_args__ = (
        Index("ix_majors_university_name", "university_id", "name"),
        CheckConstraint(_in("status", CatalogEntityStatus), name="status_valid"),
    )

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id"), nullable=False, index=True
    )
    college_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("colleges.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 学科门类（工学/理学…），来源为原目录，未在官方文件中逐条核实的按未知留空。
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CatalogEntityStatus.ACTIVE.value
    )


class Curriculum(UUIDPrimaryKey, Timestamped, Base):
    """某专业某入学年级适用的培养方案（§11.1）。

    `entry_year` 允许为空：目录来源未给出入学年级时不能臆造，留空表示待目录维护者确认。
    """

    __tablename__ = "curricula"
    __table_args__ = (
        Index("ix_curricula_major_id", "major_id"),
        CheckConstraint(_in("status", CatalogEntityStatus), name="status_valid"),
    )

    major_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("majors.id"), nullable=False
    )
    entry_year: Mapped[int | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CatalogEntityStatus.ACTIVE.value
    )


class Course(UUIDPrimaryKey, Timestamped, Base):
    """课程身份（§11.1）。

    不按名称相同自动合并；课程代码可能随方案变化，唯一性范围依据真实目录确认，
    因此这里**不加**代码唯一约束，只建查询索引（§11.2 规则 1、4）。
    """

    __tablename__ = "courses"
    __table_args__ = (
        Index("ix_courses_university_code", "university_id", "code"),
        Index("ix_courses_university_name", "university_id", "name"),
        CheckConstraint(_in("status", CourseStatus), name="status_valid"),
    )

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id"), nullable=False, index=True
    )
    # 归属学院：来源目录未记录课程归属学院时留空，课程→学院的归属通过培养方案表达。
    owning_college_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("colleges.id"), nullable=True, index=True
    )
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CourseStatus.ACTIVE.value
    )


class CurriculumCourse(UUIDPrimaryKey, Timestamped, Base):
    """课程在具体培养方案中的要求（§11.1）。

    `credits` 留空表示来源未提供可靠学分；`recommended_term` 是建议修读学期（如“大二上”）。
    """

    __tablename__ = "curriculum_courses"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "course_id", name="uq_curriculum_course"),
        CheckConstraint(
            _in("requirement_type", RequirementType), name="requirement_type_valid"
        ),
    )

    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curricula.id"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    credits: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    requirement_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RequirementType.REQUIRED.value
    )
    recommended_term: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Semester(UUIDPrimaryKey, Timestamped, Base):
    """统一学年与春秋学期表示（§11.1）。日期未知时留空，不臆造校历。"""

    __tablename__ = "semesters"
    __table_args__ = (
        UniqueConstraint("university_id", "academic_year", "season", name="uq_semester"),
        CheckConstraint(_in("season", SemesterSeason), name="season_valid"),
    )

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id"), nullable=False, index=True
    )
    # 学年形如 "2024-2025"。
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)
    season: Mapped[str] = mapped_column(String(10), nullable=False, default=SemesterSeason.FALL.value)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Teacher(UUIDPrimaryKey, Timestamped, Base):
    """仅记录必要公开教学信息，不建教师个人画像（§11.1）。"""

    __tablename__ = "teachers"

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_profile: Mapped[str | None] = mapped_column(Text, nullable=True)


class CourseOffering(UUIDPrimaryKey, Timestamped, Base):
    """一次实际开课，可对多个专业开放（通过资源/培养方案关联），教师另表（§11.1）。"""

    __tablename__ = "course_offerings"
    __table_args__ = (
        Index("ix_course_offerings_course_semester", "course_id", "semester_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    semester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semesters.id"), nullable=False, index=True
    )
    section_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    textbook: Mapped[str | None] = mapped_column(Text, nullable=True)
    syllabus: Mapped[str | None] = mapped_column(Text, nullable=True)


class OfferingTeacher(UUIDPrimaryKey, Timestamped, Base):
    """支持多位教师，不把教师固定成课程上的字符串（§11.1）。"""

    __tablename__ = "offering_teachers"
    __table_args__ = (
        UniqueConstraint("offering_id", "teacher_id", "role", name="uq_offering_teacher"),
        CheckConstraint(_in("role", TeacherRole), name="role_valid"),
    )

    offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offerings.id"), nullable=False, index=True
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teachers.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=TeacherRole.PRIMARY.value)


class CourseAlias(UUIDPrimaryKey, Timestamped, Base):
    """课程简称、旧称与检索别名，支撑“模电”“高数下”“数构期末”这类查询（§11.1、§19.1）。"""

    __tablename__ = "course_aliases"
    __table_args__ = (
        UniqueConstraint("course_id", "alias", name="uq_course_alias"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    # 来源标记（如 abbreviation/former_name/search_alias），便于区分简称与旧称。
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)


class CourseRelation(UUIDPrimaryKey, Timestamped, Base):
    """课程改名、换代码与替代关系分别记录，不把“可替代”当成“同一门课”（§11.2 规则 3、7）。

    本表不在 §3 的跨模块外键表清单内，属 catalog 自有表：记录合并原 ID 映射、
    理由与撤销依据所需的关系事实，供目录维护者使用；首期不做自动替代决策。
    """

    __tablename__ = "course_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_course_id", "target_course_id", "relation_type", name="uq_course_relation"
        ),
        CheckConstraint(
            _in("relation_type", RelationType), name="relation_type_valid"
        ),
    )

    source_course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    target_course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(20), nullable=False, default=RelationType.RELATED.value)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
