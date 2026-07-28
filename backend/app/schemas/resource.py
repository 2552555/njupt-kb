import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ResourceCreate(BaseModel):
    course_id: uuid.UUID
    title: str
    description: Optional[str] = None
    resource_type: str  # exam, ppt, note, review, lab_report, textbook, code, other
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    page_count: Optional[int] = None
    year: Optional[int] = None
    semester_season: Optional[str] = None


class ResourceUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    resource_type: Optional[str] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    page_count: Optional[int] = None
    year: Optional[int] = None
    semester_season: Optional[str] = None


class ResourceOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    uploader_id: uuid.UUID
    title: str
    description: Optional[str] = None
    resource_type: str
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    page_count: Optional[int] = None
    view_count: int
    download_count: int
    favorite_count: int
    avg_rating: float
    rating_count: int
    year: Optional[int] = None
    semester_season: Optional[str] = None
    is_verified: bool
    status: str
    created_at: datetime
    updated_at: datetime
    uploader_name: Optional[str] = None
    course_name: Optional[str] = None
    college_name: Optional[str] = None

    class Config:
        from_attributes = True
