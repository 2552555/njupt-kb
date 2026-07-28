import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("major_id", "name", name="uq_course_major_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    major_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("majors.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    course_code: Mapped[str] = mapped_column(String(50), nullable=True)
    semester: Mapped[str] = mapped_column(String(50), nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="required")
    credits: Mapped[float] = mapped_column(nullable=True)
    description: Mapped[str] = mapped_column(String(2000), nullable=True)
    teacher: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    major = relationship("Major", back_populates="courses")
    resources = relationship("Resource", back_populates="course")
