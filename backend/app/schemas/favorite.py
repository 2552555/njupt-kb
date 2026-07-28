import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class FavoriteToggle(BaseModel):
    resource_id: uuid.UUID
    folder_name: Optional[str] = "默认"


class FavoriteOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    resource_id: uuid.UUID
    folder_name: Optional[str] = "默认"
    created_at: datetime
    resource_title: Optional[str] = None
    resource_type: Optional[str] = None
    course_name: Optional[str] = None

    class Config:
        from_attributes = True
