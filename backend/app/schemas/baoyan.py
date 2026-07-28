import uuid
from datetime import datetime
from typing import Optional, Union
from pydantic import BaseModel


class BaoyanPolicyOut(BaseModel):
    id: uuid.UUID
    college_id: uuid.UUID
    year: int
    gpa_min: Optional[float] = None
    gpa_rank_requirement: Optional[str] = None
    comprehensive_formula: Optional[str] = None
    total_quota: Optional[int] = None
    regular_quota: Optional[int] = None
    special_quota: Optional[int] = None
    competition_bonus: Optional[dict] = None
    research_bonus: Optional[dict] = None
    timeline: Optional[dict] = None
    source_url: Optional[str] = None
    source_type: Optional[str] = None
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True


class BaoyanExperienceCreate(BaseModel):
    college_id: uuid.UUID
    major_id: uuid.UUID
    original_major: Optional[str] = None
    gpa: Optional[float] = None
    gpa_rank: Optional[str] = None
    target_university: str
    target_college: Optional[str] = None
    target_major: Optional[str] = None
    offer_result: Optional[str] = None
    timeline: Optional[dict] = None
    summer_camp_experience: Optional[str] = None
    pre_interview_experience: Optional[str] = None
    interview_questions: Optional[dict] = None
    advice: Optional[str] = None
    year: int
    tags: Union[list, dict, None] = None


class BaoyanExperienceOut(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    college_id: uuid.UUID
    major_id: uuid.UUID
    original_major: Optional[str] = None
    gpa: Optional[float] = None
    gpa_rank: Optional[str] = None
    target_university: str
    target_college: Optional[str] = None
    target_major: Optional[str] = None
    offer_result: Optional[str] = None
    timeline: Optional[dict] = None
    summer_camp_experience: Optional[str] = None
    pre_interview_experience: Optional[str] = None
    interview_questions: Optional[dict] = None
    advice: Optional[str] = None
    year: int
    tags: Union[list, dict, None] = None
    view_count: int
    helpful_count: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
