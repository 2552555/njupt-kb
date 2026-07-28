import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, CheckConstraint, func, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class Competition(Base):
    __tablename__ = "competitions"
    __table_args__ = (
        CheckConstraint("difficulty >= 1 AND difficulty <= 5", name="ck_competition_difficulty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=True)
    organizer: Mapped[str] = mapped_column(String(200), nullable=True)
    official_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    registration_period: Mapped[str] = mapped_column(String(200), nullable=True)
    competition_period: Mapped[str] = mapped_column(String(200), nullable=True)
    suitable_majors: Mapped[dict] = mapped_column(JSON, nullable=True)
    suitable_grades: Mapped[dict] = mapped_column(JSON, nullable=True)
    team_size: Mapped[str] = mapped_column(String(100), nullable=True)
    njupt_contact: Mapped[str] = mapped_column(String(200), nullable=True)
    njupt_winners_count: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    winners = relationship("CompetitionWinner", back_populates="competition")


class CompetitionWinner(Base):
    __tablename__ = "competition_winners"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    competition_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("competitions.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    award_level: Mapped[str] = mapped_column(String(100), nullable=False)
    team_members: Mapped[dict] = mapped_column(JSON, nullable=True)
    advisor: Mapped[str] = mapped_column(String(100), nullable=True)
    project_title: Mapped[str] = mapped_column(String(500), nullable=True)
    experience_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    competition = relationship("Competition", back_populates="winners")
