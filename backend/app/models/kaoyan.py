import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, func, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class KaoyanSchool(Base):
    __tablename__ = "kaoyan_schools"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_njupt: Mapped[bool] = mapped_column(Boolean, default=False)
    website_url: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    majors = relationship("KaoyanMajor", back_populates="school")


class KaoyanMajor(Base):
    __tablename__ = "kaoyan_majors"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("kaoyan_schools.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    college: Mapped[str] = mapped_column(String(200), nullable=True)
    exam_subjects: Mapped[dict] = mapped_column(JSON, nullable=True)
    reference_books: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    school = relationship("KaoyanSchool", back_populates="majors")
    score_lines = relationship("KaoyanScoreLine", back_populates="kaoyan_major")


class KaoyanScoreLine(Base):
    __tablename__ = "kaoyan_score_lines"
    __table_args__ = (
        UniqueConstraint("kaoyan_major_id", "year", name="uq_scoreline_major_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kaoyan_major_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("kaoyan_majors.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score_line: Mapped[int] = mapped_column(Integer, nullable=True)
    political_line: Mapped[int] = mapped_column(Integer, nullable=True)
    english_line: Mapped[int] = mapped_column(Integer, nullable=True)
    business1_line: Mapped[int] = mapped_column(Integer, nullable=True)
    business2_line: Mapped[int] = mapped_column(Integer, nullable=True)
    applicants: Mapped[int] = mapped_column(Integer, nullable=True)
    admitted: Mapped[int] = mapped_column(Integer, nullable=True)
    lowest_score: Mapped[int] = mapped_column(Integer, nullable=True)
    highest_score: Mapped[int] = mapped_column(Integer, nullable=True)
    avg_score: Mapped[float] = mapped_column(Float, nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    kaoyan_major = relationship("KaoyanMajor", back_populates="score_lines")
