import uuid
from datetime import datetime, timezone

from sqlalchemy import Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Download(Base):
    __tablename__ = "downloads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("resources.id"), nullable=False)
    cost_points: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", back_populates="downloads")
    resource = relationship("Resource", back_populates="downloads")
