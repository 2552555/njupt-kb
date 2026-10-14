"""历史专题模型（设计文档 §3、§33）。

本模块保留原仓库的专题功能表（保研 / 考研 / 竞赛 / 转专业 / 抓取）。表名与业务字段
与原实现保持一致，只做迁移必需的最小改动：

- 声明基类 `app.database.Base` → `app.db.Base`；
- 主键统一改用 `app.db.UUIDPrimaryKey`，时间戳改用 `app.db.Timestamped` 混入
  （原表均已是 UUID 主键，无 Integer 自增主键需要保留）；
- 跨模块外键只写字符串表名（`colleges.id`、`majors.id`、`users.id`），不导入对方模型类；
- 指向已移出本模块的 `identity.User` 的反向 `relationship` 已删除，改为注释说明
  （原用户模型已重写，不再持有 `baoyan_experiences` / `transfer_cases` 反向关系）。
  模块内部的关联（竞赛-获奖、考研学校-专业-分数线、抓取源-页面）保留。

数据来源未经核实：来源不明的记录保留原 `source_url`，`is_verified` 默认 False。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = [
    "BaoyanPolicy",
    "BaoyanExperience",
    "Competition",
    "CompetitionWinner",
    "KaoyanSchool",
    "KaoyanMajor",
    "KaoyanScoreLine",
    "MajorTransferPolicy",
    "MajorTransferCase",
    "ScrapedSource",
    "ScrapedPage",
]


# --------------------------------------------------------------------------- #
# 保研
# --------------------------------------------------------------------------- #
class BaoyanPolicy(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "baoyan_policies"
    __table_args__ = (
        UniqueConstraint("college_id", "year", name="uq_baoyan_policy_college_year"),
    )

    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    gpa_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpa_rank_requirement: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comprehensive_formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regular_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    special_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competition_bonus: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    research_bonus: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timeline: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class BaoyanExperience(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "baoyan_experiences"

    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    major_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("majors.id"), nullable=False)
    original_major: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpa_rank: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_university: Mapped[str] = mapped_column(String(200), nullable=False)
    target_college: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_major: Mapped[str | None] = mapped_column(String(200), nullable=True)
    offer_result: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timeline: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summer_camp_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    pre_interview_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_questions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    advice: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # 原 `author = relationship("User", back_populates="baoyan_experiences")` 已删除：
    # 该关联指向已移出本模块的 identity.User，且重写后的 User 不再持有对应反向关系。
    # 保留 `author_id` 外键，需要作者信息时由调用方另行查询（§2.2）。


# --------------------------------------------------------------------------- #
# 竞赛
# --------------------------------------------------------------------------- #
class Competition(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "competitions"
    __table_args__ = (
        CheckConstraint("difficulty >= 1 AND difficulty <= 5", name="ck_competition_difficulty"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    organizer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    official_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_period: Mapped[str | None] = mapped_column(String(200), nullable=True)
    competition_period: Mapped[str | None] = mapped_column(String(200), nullable=True)
    suitable_majors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suitable_grades: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    team_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    njupt_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    njupt_winners_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    winners = relationship("CompetitionWinner", back_populates="competition")


class CompetitionWinner(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "competition_winners"

    competition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("competitions.id"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    award_level: Mapped[str] = mapped_column(String(100), nullable=False)
    team_members: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    advisor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    project_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    experience_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    competition = relationship("Competition", back_populates="winners")


# --------------------------------------------------------------------------- #
# 考研
# --------------------------------------------------------------------------- #
class KaoyanSchool(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "kaoyan_schools"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_njupt: Mapped[bool] = mapped_column(Boolean, default=False)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    majors = relationship("KaoyanMajor", back_populates="school")


class KaoyanMajor(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "kaoyan_majors"

    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("kaoyan_schools.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    college: Mapped[str | None] = mapped_column(String(200), nullable=True)
    exam_subjects: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reference_books: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    school = relationship("KaoyanSchool", back_populates="majors")
    score_lines = relationship("KaoyanScoreLine", back_populates="kaoyan_major")


class KaoyanScoreLine(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "kaoyan_score_lines"
    __table_args__ = (
        UniqueConstraint("kaoyan_major_id", "year", name="uq_scoreline_major_year"),
    )

    kaoyan_major_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("kaoyan_majors.id"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    political_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    english_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business1_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business2_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applicants: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admitted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lowest_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    highest_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    kaoyan_major = relationship("KaoyanMajor", back_populates="score_lines")


# --------------------------------------------------------------------------- #
# 转专业
# --------------------------------------------------------------------------- #
class MajorTransferPolicy(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "major_transfer_policies"

    year: Mapped[int] = mapped_column(Integer, nullable=False)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    major_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("majors.id"), nullable=True)
    quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    exam_subjects: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    interview_required: Mapped[bool] = mapped_column(Boolean, default=False)
    application_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    application_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class MajorTransferCase(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "major_transfer_cases"

    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    from_major: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_college: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_major: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_college: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    exam_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    experience_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[bool] = mapped_column(Boolean, default=True)

    # 原 `author = relationship("User", back_populates="transfer_cases")` 已删除，
    # 原因同 BaoyanExperience：identity.User 已重写，不再持有该反向关系。


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
class ScrapedSource(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "scraped_sources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    spider_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    schedule: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    pages = relationship("ScrapedPage", back_populates="source")


class ScrapedPage(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "scraped_pages"

    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("scraped_sources.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2000), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    source = relationship("ScrapedSource", back_populates="pages")
