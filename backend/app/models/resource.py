import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, JSON, Boolean, func, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("courses.id"), nullable=False)
    uploader_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    resource_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # exam, ppt, note, review, lab_report, textbook, code, other
    file_path: Mapped[str] = mapped_column(String(1000), nullable=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=True)
    file_type: Mapped[str] = mapped_column(String(50), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    favorite_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_rating: Mapped[float] = mapped_column(Float, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    year: Mapped[int] = mapped_column(Integer, nullable=True)
    semester_season: Mapped[str] = mapped_column(String(20), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, active, reported, deleted
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    uploader = relationship("User", back_populates="resources")
    course = relationship("Course", back_populates="resources")
    tags = relationship("ResourceTag", back_populates="resource")
    favorites = relationship("Favorite", back_populates="resource")
    comments = relationship("Comment", back_populates="resource")
    downloads = relationship("Download", back_populates="resource")
