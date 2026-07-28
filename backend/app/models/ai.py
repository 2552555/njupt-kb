import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, func, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class AIChatHistory(Base):
    __tablename__ = "ai_chat_histories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    session_type: Mapped[str] = mapped_column(String(50), nullable=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=True)
    ai_response: Mapped[str] = mapped_column(Text, nullable=True)
    model_used: Mapped[str] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", back_populates="ai_chat_histories")


class AIStudyPlan(Base):
    __tablename__ = "ai_study_plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(50), nullable=True)
    input_params: Mapped[dict] = mapped_column(JSON, nullable=True)
    plan_content: Mapped[dict] = mapped_column(JSON, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", back_populates="ai_study_plans")
