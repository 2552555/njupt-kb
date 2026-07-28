import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class UserRegister(BaseModel):
    email: str
    password: str
    display_name: str
    student_id: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_njupt_email(cls, v: str) -> str:
        if not v.endswith("@njupt.edu.cn"):
            raise ValueError("Must use NJUPT email (@njupt.edu.cn)")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class UserLogin(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: Optional[str] = None
    student_id: Optional[str] = None
    avatar_url: Optional[str] = None
    college_id: Optional[uuid.UUID] = None
    major_id: Optional[uuid.UUID] = None
    enrollment_year: Optional[int] = None
    points: int
    role: str
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    student_id: Optional[str] = None
    avatar_url: Optional[str] = None
    college_id: Optional[uuid.UUID] = None
    major_id: Optional[uuid.UUID] = None
    enrollment_year: Optional[int] = None
