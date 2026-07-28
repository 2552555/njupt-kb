import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


class CommentCreate(BaseModel):
    content: Optional[str] = None
    rating: Optional[int] = None

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 5):
            raise ValueError("Rating must be between 1 and 5")
        return v


class CommentOut(BaseModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    user_id: uuid.UUID
    content: Optional[str] = None
    rating: Optional[int] = None
    parent_id: Optional[uuid.UUID] = None
    created_at: datetime
    user_display_name: Optional[str] = None
    user_avatar_url: Optional[str] = None

    class Config:
        from_attributes = True
