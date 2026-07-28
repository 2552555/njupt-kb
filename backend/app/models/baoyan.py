import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, func, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class BaoyanPolicy(Base):
    __tablename__ = "baoyan_policies"
    __table_args__ = (
        UniqueConstraint("college_id", "year", name="uq_baoyan_policy_college_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    gpa_min: Mapped[float] = mapped_column(Float, nullable=True)
    gpa_rank_requirement: Mapped[str] = mapped_column(String(200), nullable=True)
    comprehensive_formula: Mapped[str] = mapped_column(Text, nullable=True)
    total_quota: Mapped[int] = mapped_column(Integer, nullable=True)
    regular_quota: Mapped[int] = mapped_column(Integer, nullable=True)
    special_quota: Mapped[int] = mapped_column(Integer, nullable=True)
    competition_bonus: Mapped[dict] = mapped_column(JSON, nullable=True)
    research_bonus: Mapped[dict] = mapped_column(JSON, nullable=True)
    timeline: Mapped[dict] = mapped_column(JSON, nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BaoyanExperience(Base):
    __tablename__ = "baoyan_experiences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    major_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("majors.id"), nullable=False)
    original_major: Mapped[str] = mapped_column(String(200), nullable=True)
    gpa: Mapped[float] = mapped_column(Float, nullable=True)
    gpa_rank: Mapped[str] = mapped_column(String(50), nullable=True)
    target_university: Mapped[str] = mapped_column(String(200), nullable=False)
    target_college: Mapped[str] = mapped_column(String(200), nullable=True)
    target_major: Mapped[str] = mapped_column(String(200), nullable=True)
    offer_result: Mapped[str] = mapped_column(String(50), nullable=True)
    timeline: Mapped[dict] = mapped_column(JSON, nullable=True)
    summer_camp_experience: Mapped[str] = mapped_column(Text, nullable=True)
    pre_interview_experience: Mapped[str] = mapped_column(Text, nullable=True)
    interview_questions: Mapped[dict] = mapped_column(JSON, nullable=True)
    advice: Mapped[str] = mapped_column(Text, nullable=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[dict] = mapped_column(JSON, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    author = relationship("User", back_populates="baoyan_experiences")
