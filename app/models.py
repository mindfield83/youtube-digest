"""
SQLAlchemy Models for YouTube Digest

Tables:
- channels: Subscribed YouTube channels
- processed_videos: Videos that have been processed
- digest_history: History of sent digest emails
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Channel(Base):
    """Subscribed YouTube channels."""

    __tablename__ = "channels"

    channel_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_url: Mapped[Optional[str]] = mapped_column(String(500))
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)  # YouTube channel description

    # Manual category override (if set, AI categorization is skipped)
    manual_category: Mapped[Optional[str]] = mapped_column(String(100))

    # Tracking
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    videos: Mapped[List["ProcessedVideo"]] = relationship(
        "ProcessedVideo", back_populates="channel"
    )

    def __repr__(self) -> str:
        return f"<Channel {self.channel_name}>"


class ProcessedVideo(Base):
    """Videos that have been processed (transcribed + summarized)."""

    __tablename__ = "processed_videos"

    video_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("channels.channel_id"), nullable=False
    )

    # Video metadata
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Processing results
    category: Mapped[str] = mapped_column(String(100), default="Sonstige")
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    transcript_source: Mapped[Optional[str]] = mapped_column(
        String(50)  # "youtube", "supadata", "failed"
    )

    # AI Summary (structured JSON)
    summary: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Expected structure:
    # {
    #     "core_message": "...",
    #     "detailed_summary": "...",
    #     "key_takeaways": ["...", "..."],
    #     "timestamps": [{"time": "00:00", "description": "..."}],
    #     "action_items": ["..."]
    # }

    # Processing status
    processed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    processing_status: Mapped[str] = mapped_column(
        String(50), default="pending"
        # "pending", "processing", "completed", "failed", "retry_scheduled"
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Digest tracking
    included_in_digest_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("digest_history.id")
    )

    # Relationships
    channel: Mapped["Channel"] = relationship("Channel", back_populates="videos")
    digest: Mapped[Optional["DigestHistory"]] = relationship(
        "DigestHistory", back_populates="videos"
    )

    def __repr__(self) -> str:
        return f"<ProcessedVideo {self.video_id}: {self.title[:50]}>"

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def duration_formatted(self) -> str:
        """Format duration as HH:MM:SS or MM:SS."""
        hours, remainder = divmod(self.duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class DigestHistory(Base):
    """History of sent digest emails."""

    __tablename__ = "digest_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Timing
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Statistics
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    total_duration_seconds: Mapped[int] = mapped_column(Integer, default=0)

    # Category breakdown (JSON)
    category_counts: Mapped[Optional[dict]] = mapped_column(JSONB)
    # {"Claude Code": 5, "Coding/AI Allgemein": 3, ...}

    # Email status
    email_status: Mapped[str] = mapped_column(
        String(50), default="pending"
        # "pending", "sent", "failed"
    )
    email_error: Mapped[Optional[str]] = mapped_column(Text)
    recipient_email: Mapped[str] = mapped_column(String(255))

    # Trigger reason
    trigger_reason: Mapped[str] = mapped_column(
        String(50), default="interval"
        # "interval", "threshold", "manual"
    )

    # Relationships
    videos: Mapped[List["ProcessedVideo"]] = relationship(
        "ProcessedVideo", back_populates="digest"
    )

    def __repr__(self) -> str:
        return f"<DigestHistory {self.id}: {self.video_count} videos>"

    @property
    def total_duration_formatted(self) -> str:
        """Format total duration as human-readable string."""
        hours, remainder = divmod(self.total_duration_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}min"
        return f"{minutes}min"


class OAuthToken(Base):
    """Store OAuth tokens (for token refresh tracking)."""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(50), unique=True)  # "youtube"

    # Token data (encrypted in production)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    token_uri: Mapped[str] = mapped_column(String(500))

    # Expiry tracking
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_refreshed: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<OAuthToken {self.service}>"

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.utcnow() >= self.expires_at


# Database engine and session
engine = create_engine(settings.database_url, echo=settings.app_debug)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency for FastAPI to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)
