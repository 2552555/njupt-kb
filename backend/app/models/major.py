import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Major(Base):
    __tablename__ = "majors"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("colleges.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    degree: Mapped[str] = mapped_column(String(50), nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    college = relationship("College", back_populates="majors")
    courses = relationship("Course", back_populates="major")
    users = relationship("User", back_populates="major")
