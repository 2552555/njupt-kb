import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TransferPolicyOut(BaseModel):
    id: uuid.UUID
    year: int
    college_id: uuid.UUID
    major_id: Optional[uuid.UUID] = None
    quota: Optional[int] = None
    requirements: Optional[dict] = None
    exam_subjects: Optional[dict] = None
    interview_required: bool
    application_start: Optional[datetime] = None
    application_end: Optional[datetime] = None
    source_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TransferCaseCreate(BaseModel):
    year: int
    from_major: Optional[str] = None
    from_college: Optional[str] = None
    to_major: Optional[str] = None
    to_college: Optional[str] = None
    gpa: Optional[float] = None
    exam_scores: Optional[dict] = None
    experience_text: Optional[str] = None
    result: bool = True


class TransferCaseOut(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    year: int
    from_major: Optional[str] = None
    from_college: Optional[str] = None
    to_major: Optional[str] = None
    to_college: Optional[str] = None
    gpa: Optional[float] = None
    exam_scores: Optional[dict] = None
    experience_text: Optional[str] = None
    result: bool
    created_at: datetime

    class Config:
        from_attributes = True
