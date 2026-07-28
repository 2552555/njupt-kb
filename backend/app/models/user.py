import uuid
from datetime import datetime, timezone
from typing import List

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=True)
    student_id: Mapped[str] = mapped_column(String(50), nullable=True)
    avatar_url: Mapped[str] = mapped_column(String(500), nullable=True)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=True)
    major_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("majors.id"), nullable=True)
    enrollment_year: Mapped[int] = mapped_column(Integer, nullable=True)
    points: Mapped[int] = mapped_column(Integer, default=100)
    role: Mapped[str] = mapped_column(String(20), default="student")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    college = relationship("College", back_populates="users")
    major = relationship("Major", back_populates="users")
    resources = relationship("Resource", back_populates="uploader")
    favorites = relationship("Favorite", back_populates="user")
    comments = relationship("Comment", back_populates="user")
    downloads = relationship("Download", back_populates="user")
    points_logs = relationship("PointsLog", back_populates="user")
    baoyan_experiences = relationship("BaoyanExperience", back_populates="author")
    transfer_cases = relationship("MajorTransferCase", back_populates="author")
    ai_chat_histories = relationship("AIChatHistory", back_populates="user")
    ai_study_plans = relationship("AIStudyPlan", back_populates="user")
    contribution_days = relationship("ContributionDay", back_populates="user")
    notifications = relationship("Notification", back_populates="user")
