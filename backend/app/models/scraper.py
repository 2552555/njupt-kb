import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, func, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Uuid

from app.database import Base


class ScrapedSource(Base):
    __tablename__ = "scraped_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    spider_name: Mapped[str] = mapped_column(String(200), nullable=True)
    schedule: Mapped[str] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pages = relationship("ScrapedPage", back_populates="source")


class ScrapedPage(Base):
    __tablename__ = "scraped_pages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("scraped_sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=True)
    content_html: Mapped[str] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    source = relationship("ScrapedSource", back_populates="pages")
