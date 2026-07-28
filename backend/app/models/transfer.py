import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, func, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class MajorTransferPolicy(Base):
    __tablename__ = "major_transfer_policies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    major_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("majors.id"), nullable=True)
    quota: Mapped[int] = mapped_column(Integer, nullable=True)
    requirements: Mapped[dict] = mapped_column(JSON, nullable=True)
    exam_subjects: Mapped[dict] = mapped_column(JSON, nullable=True)
    interview_required: Mapped[bool] = mapped_column(Boolean, default=False)
    application_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    application_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MajorTransferCase(Base):
    __tablename__ = "major_transfer_cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    from_major: Mapped[str] = mapped_column(String(200), nullable=True)
    from_college: Mapped[str] = mapped_column(String(200), nullable=True)
    to_major: Mapped[str] = mapped_column(String(200), nullable=True)
    to_college: Mapped[str] = mapped_column(String(200), nullable=True)
    gpa: Mapped[float] = mapped_column(Float, nullable=True)
    exam_scores: Mapped[dict] = mapped_column(JSON, nullable=True)
    experience_text: Mapped[str] = mapped_column(Text, nullable=True)
    result: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    author = relationship("User", back_populates="transfer_cases")
