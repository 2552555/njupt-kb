import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, CheckConstraint, func, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_comment_rating"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("resources.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=True)
    parent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("comments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    resource = relationship("Resource", back_populates="comments")
    user = relationship("User", back_populates="comments")
    replies = relationship("Comment", backref="parent", remote_side="Comment.id")
