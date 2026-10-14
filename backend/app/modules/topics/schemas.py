"""topics 对外 DTO（设计文档 §10、§25.1）。

- 一律返回 DTO，不返回 ORM 对象；id 以字符串序列化（前端类型为 string）。
- 列表接口统一用 `app.core.pagination.CursorPage`（items / next_cursor / has_more），
  不再使用原实现的 `{query, type, results}` 或 total/page/page_size/pages 结构。
- 本模块为历史专题功能保留，字段沿用原实现，不新增编造的政策或数据字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _TopicOut(BaseModel):
    """所有对外 DTO 的基类：允许从 ORM 对象构造（Pydantic v2）。"""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# 保研
# --------------------------------------------------------------------------- #
class BaoyanPolicyOut(_TopicOut):
    id: uuid.UUID
    college_id: uuid.UUID
    year: int
    gpa_min: float | None = None
    gpa_rank_requirement: str | None = None
    comprehensive_formula: str | None = None
    total_quota: int | None = None
    regular_quota: int | None = None
    special_quota: int | None = None
    competition_bonus: dict | None = None
    research_bonus: dict | None = None
    timeline: dict | None = None
    source_url: str | None = None
    source_type: str | None = None
    is_verified: bool
    created_at: datetime


class BaoyanExperienceCreate(BaseModel):
    college_id: uuid.UUID
    major_id: uuid.UUID
    original_major: str | None = None
    gpa: float | None = None
    gpa_rank: str | None = None
    target_university: str = Field(min_length=1, max_length=200)
    target_college: str | None = None
    target_major: str | None = None
    offer_result: str | None = None
    timeline: dict | None = None
    summer_camp_experience: str | None = None
    pre_interview_experience: str | None = None
    interview_questions: dict | None = None
    advice: str | None = None
    year: int
    tags: list | dict | None = None


class BaoyanExperienceOut(_TopicOut):
    id: uuid.UUID
    author_id: uuid.UUID
    college_id: uuid.UUID
    major_id: uuid.UUID
    original_major: str | None = None
    gpa: float | None = None
    gpa_rank: str | None = None
    target_university: str
    target_college: str | None = None
    target_major: str | None = None
    offer_result: str | None = None
    timeline: dict | None = None
    summer_camp_experience: str | None = None
    pre_interview_experience: str | None = None
    interview_questions: dict | None = None
    advice: str | None = None
    year: int
    tags: list | dict | None = None
    view_count: int
    helpful_count: int
    status: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# 竞赛
# --------------------------------------------------------------------------- #
class CompetitionWinnerOut(_TopicOut):
    id: uuid.UUID
    competition_id: uuid.UUID
    year: int
    award_level: str
    team_members: list | dict | None = None
    advisor: str | None = None
    project_title: str | None = None
    experience_text: str | None = None
    created_at: datetime


class CompetitionListOut(_TopicOut):
    id: uuid.UUID
    name: str
    category: str | None = None
    difficulty: int | None = None
    organizer: str | None = None
    official_url: str | None = None
    description: str | None = None
    registration_period: str | None = None
    competition_period: str | None = None
    suitable_majors: list | dict | None = None
    suitable_grades: list | dict | None = None
    team_size: str | None = None
    njupt_contact: str | None = None
    njupt_winners_count: int | None = None
    created_at: datetime


class CompetitionOut(CompetitionListOut):
    winners: list[CompetitionWinnerOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 考研
# --------------------------------------------------------------------------- #
class KaoyanSchoolOut(_TopicOut):
    id: uuid.UUID
    name: str
    is_njupt: bool
    website_url: str | None = None
    created_at: datetime


class KaoyanMajorOut(_TopicOut):
    id: uuid.UUID
    school_id: uuid.UUID
    name: str
    college: str | None = None
    exam_subjects: dict | None = None
    reference_books: dict | None = None
    created_at: datetime


class KaoyanScoreLineOut(_TopicOut):
    id: uuid.UUID
    kaoyan_major_id: uuid.UUID
    year: int
    total_score_line: int | None = None
    political_line: int | None = None
    english_line: int | None = None
    business1_line: int | None = None
    business2_line: int | None = None
    applicants: int | None = None
    admitted: int | None = None
    lowest_score: int | None = None
    highest_score: int | None = None
    avg_score: float | None = None
    source_url: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# 转专业
# --------------------------------------------------------------------------- #
class TransferPolicyOut(_TopicOut):
    id: uuid.UUID
    year: int
    college_id: uuid.UUID
    major_id: uuid.UUID | None = None
    quota: int | None = None
    requirements: dict | None = None
    exam_subjects: dict | None = None
    interview_required: bool
    application_start: datetime | None = None
    application_end: datetime | None = None
    source_url: str | None = None
    created_at: datetime


class TransferCaseCreate(BaseModel):
    year: int
    from_major: str | None = None
    from_college: str | None = None
    to_major: str | None = None
    to_college: str | None = None
    gpa: float | None = None
    exam_scores: dict | None = None
    experience_text: str | None = None
    result: bool = True


class TransferCaseOut(_TopicOut):
    id: uuid.UUID
    author_id: uuid.UUID
    year: int
    from_major: str | None = None
    from_college: str | None = None
    to_major: str | None = None
    to_college: str | None = None
    gpa: float | None = None
    exam_scores: dict | None = None
    experience_text: str | None = None
    result: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
class ScrapedPageOut(_TopicOut):
    id: uuid.UUID
    source_id: uuid.UUID
    url: str
    title: str | None = None
    content_text: str | None = None
    published_at: datetime | None = None
    scraped_at: datetime
    category: str | None = None
