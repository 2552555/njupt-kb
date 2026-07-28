import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    resources = relationship("ResourceTag", back_populates="tag")


class ResourceTag(Base):
    __tablename__ = "resource_tags"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tags.id"), primary_key=True
    )

    resource = relationship("Resource", back_populates="tags")
    tag = relationship("Tag", back_populates="resources")
