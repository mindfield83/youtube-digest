# app/api/schemas.py
"""Pydantic schemas for API request/response validation."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Channel Schemas
# ============================================================================


class ChannelResponse(BaseModel):
    """Response schema for a YouTube channel."""

    channel_id: str
    channel_name: str
    channel_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    manual_category: Optional[str] = None
    subscribed_at: Optional[datetime] = None
    last_checked: Optional[datetime] = None
    is_active: bool = True
    video_count: int = 0

    model_config = {"from_attributes": True}


class ChannelListResponse(BaseModel):
    """Response schema for channel list."""

    channels: list[ChannelResponse]
    total: int


# ============================================================================
# Video Schemas
# ============================================================================


class VideoSummaryResponse(BaseModel):
    """Response schema for video summary details."""

    core_message: str
    detailed_summary: str
    key_takeaways: list[str]
    timestamps: list[dict[str, str]] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


class VideoResponse(BaseModel):
    """Response schema for a processed video."""

    video_id: str
    title: str
    description: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    duration_seconds: int = 0
    published_at: Optional[datetime] = None
    thumbnail_url: Optional[str] = None
    category: Optional[str] = None
    processing_status: str = "pending"
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    summary: Optional[VideoSummaryResponse] = None
    youtube_url: Optional[str] = None

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: Any) -> None:
        """Set computed fields after initialization."""
        if self.youtube_url is None and self.video_id:
            object.__setattr__(
                self, "youtube_url", f"https://www.youtube.com/watch?v={self.video_id}"
            )


class VideoListResponse(BaseModel):
    """Response schema for video list with pagination."""

    videos: list[VideoResponse]
    total: int
    page: int = 1
    page_size: int = 20


class VideoFilterParams(BaseModel):
    """Query parameters for filtering videos."""

    category: Optional[str] = None
    status: Optional[str] = None
    channel_id: Optional[str] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# ============================================================================
# Digest Schemas
# ============================================================================


class DigestResponse(BaseModel):
    """Response schema for a digest history entry."""

    id: int
    sent_at: Optional[datetime] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    video_count: int = 0
    total_duration_seconds: int = 0
    category_counts: dict[str, int] = Field(default_factory=dict)
    email_status: str = "pending"
    trigger_reason: str = "scheduled"

    model_config = {"from_attributes": True}


class DigestListResponse(BaseModel):
    """Response schema for digest history list."""

    digests: list[DigestResponse]
    total: int


class TriggerDigestRequest(BaseModel):
    """Request schema for manual digest trigger."""

    force: bool = False  # Send even if no new videos


class TriggerDigestResponse(BaseModel):
    """Response schema for digest trigger."""

    task_id: str
    status: str
    message: str


# ============================================================================
# System Status Schemas
# ============================================================================


class OAuthStatus(BaseModel):
    """OAuth token status."""

    valid: bool
    expires_at: Optional[datetime] = None
    last_refreshed: Optional[datetime] = None


class WorkerStatus(BaseModel):
    """Celery worker status."""

    active: bool
    queued_tasks: int = 0
    active_tasks: int = 0


class SystemStatus(BaseModel):
    """Overall system status."""

    oauth_valid: bool
    worker_active: bool
    pending_videos: int = 0
    processing_videos: int = 0
    completed_videos: int = 0
    failed_videos: int = 0
    last_check_at: Optional[datetime] = None
    last_digest_at: Optional[datetime] = None
    next_scheduled_digest: Optional[datetime] = None
    total_channels: int = 0


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    database: bool = True
    redis: bool = True
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


# ============================================================================
# Task Schemas
# ============================================================================


class TaskStatusResponse(BaseModel):
    """Response schema for task status check."""

    task_id: str
    status: str  # PENDING, STARTED, SUCCESS, FAILURE, RETRY
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
