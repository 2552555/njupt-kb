import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class KaoyanSchoolOut(BaseModel):
    id: uuid.UUID
    name: str
    is_njupt: bool
    website_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class KaoyanMajorOut(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    name: str
    college: Optional[str] = None
    exam_subjects: Optional[dict] = None
    reference_books: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class KaoyanScoreLineOut(BaseModel):
    id: uuid.UUID
    kaoyan_major_id: uuid.UUID
    year: int
    total_score_line: Optional[int] = None
    political_line: Optional[int] = None
    english_line: Optional[int] = None
    business1_line: Optional[int] = None
    business2_line: Optional[int] = None
    applicants: Optional[int] = None
    admitted: Optional[int] = None
    lowest_score: Optional[int] = None
    highest_score: Optional[int] = None
    avg_score: Optional[float] = None
    source_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
