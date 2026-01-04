# app/api/routes.py
"""API route handlers for YouTube Digest dashboard."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.api.schemas import (
    ChannelListResponse,
    ChannelResponse,
    DigestListResponse,
    DigestResponse,
    HealthResponse,
    SystemStatus,
    TaskStatusResponse,
    TriggerDigestRequest,
    TriggerDigestResponse,
    VideoListResponse,
    VideoResponse,
    VideoSummaryResponse,
)
from app.celery_app import celery_app
from app.models import Channel, DigestHistory, OAuthToken, ProcessedVideo, get_db
from app.tasks import generate_and_send_digest

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Health Check
# ============================================================================


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint.

    Returns database and Redis connectivity status.
    """
    try:
        # Check database
        db.execute(text("SELECT 1"))
        db_healthy = True
    except Exception:
        db_healthy = False

    try:
        # Check Redis via Celery
        celery_app.control.ping(timeout=1)
        redis_healthy = True
    except Exception:
        redis_healthy = False

    status = "healthy" if (db_healthy and redis_healthy) else "degraded"

    return HealthResponse(
        status=status,
        database=db_healthy,
        redis=redis_healthy,
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================================
# System Status
# ============================================================================


@router.get("/api/status", response_model=SystemStatus, tags=["Status"])
async def get_system_status(db: Session = Depends(get_db)):
    """
    Get overall system status.

    Returns video counts, last activity, OAuth status.
    """
    # Video counts by status
    pending = db.query(ProcessedVideo).filter(
        ProcessedVideo.processing_status == "pending"
    ).count()

    processing = db.query(ProcessedVideo).filter(
        ProcessedVideo.processing_status == "processing"
    ).count()

    completed = db.query(ProcessedVideo).filter(
        ProcessedVideo.processing_status == "completed"
    ).count()

    failed = db.query(ProcessedVideo).filter(
        ProcessedVideo.processing_status == "failed"
    ).count()

    # Last check (from channel last_checked)
    last_check = db.query(func.max(Channel.last_checked)).scalar()

    # Last digest
    last_digest = db.query(DigestHistory).filter(
        DigestHistory.email_status == "sent"
    ).order_by(DigestHistory.sent_at.desc()).first()

    # OAuth status
    oauth_token = db.query(OAuthToken).filter(
        OAuthToken.service == "youtube"
    ).first()
    oauth_valid = oauth_token is not None and not oauth_token.is_expired

    # Total channels
    total_channels = db.query(Channel).filter(Channel.is_active == True).count()

    # Worker status (try to ping)
    try:
        inspect = celery_app.control.inspect()
        active = inspect.active()
        worker_active = active is not None and len(active) > 0
    except Exception:
        worker_active = False

    return SystemStatus(
        oauth_valid=oauth_valid,
        worker_active=worker_active,
        pending_videos=pending,
        processing_videos=processing,
        completed_videos=completed,
        failed_videos=failed,
        last_check_at=last_check,
        last_digest_at=last_digest.sent_at if last_digest else None,
        next_scheduled_digest=None,  # TODO: Calculate from beat schedule
        total_channels=total_channels,
    )


# ============================================================================
# Channels
# ============================================================================


@router.get("/api/channels", response_model=ChannelListResponse, tags=["Channels"])
async def list_channels(
    active_only: bool = Query(True, description="Only show active channels"),
    db: Session = Depends(get_db),
):
    """
    List all subscribed YouTube channels.
    """
    query = db.query(Channel)

    if active_only:
        query = query.filter(Channel.is_active == True)

    channels = query.order_by(Channel.channel_name).all()

    # Add video counts
    channel_responses = []
    for channel in channels:
        video_count = db.query(ProcessedVideo).filter(
            ProcessedVideo.channel_id == channel.channel_id
        ).count()

        channel_responses.append(
            ChannelResponse(
                channel_id=channel.channel_id,
                channel_name=channel.channel_name,
                channel_url=channel.channel_url,
                thumbnail_url=channel.thumbnail_url,
                manual_category=channel.manual_category,
                subscribed_at=channel.subscribed_at,
                last_checked=channel.last_checked,
                is_active=channel.is_active,
                video_count=video_count,
            )
        )

    return ChannelListResponse(
        channels=channel_responses,
        total=len(channel_responses),
    )


# ============================================================================
# Videos
# ============================================================================


@router.get("/api/videos", response_model=VideoListResponse, tags=["Videos"])
async def list_videos(
    category: Optional[str] = Query(None, description="Filter by category"),
    status: Optional[str] = Query(None, description="Filter by processing status"),
    channel_id: Optional[str] = Query(None, description="Filter by channel"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
):
    """
    List processed videos with optional filtering and pagination.
    """
    query = db.query(ProcessedVideo)

    # Apply filters
    if category:
        query = query.filter(ProcessedVideo.category == category)
    if status:
        query = query.filter(ProcessedVideo.processing_status == status)
    if channel_id:
        query = query.filter(ProcessedVideo.channel_id == channel_id)

    # Get total count
    total = query.count()

    # Apply pagination
    offset = (page - 1) * page_size
    videos = query.order_by(
        ProcessedVideo.published_at.desc()
    ).offset(offset).limit(page_size).all()

    # Build responses with channel names
    video_responses = []
    for video in videos:
        channel = db.query(Channel).filter(
            Channel.channel_id == video.channel_id
        ).first()

        summary = None
        if video.summary:
            summary = VideoSummaryResponse(
                core_message=video.summary.get("core_message", ""),
                detailed_summary=video.summary.get("detailed_summary", ""),
                key_takeaways=video.summary.get("key_takeaways", []),
                timestamps=video.summary.get("timestamps", []),
                action_items=video.summary.get("action_items", []),
            )

        video_responses.append(
            VideoResponse(
                video_id=video.video_id,
                title=video.title,
                description=video.description,
                channel_id=video.channel_id,
                channel_name=channel.channel_name if channel else None,
                duration_seconds=video.duration_seconds,
                published_at=video.published_at,
                thumbnail_url=video.thumbnail_url,
                category=video.category,
                processing_status=video.processing_status,
                processed_at=video.processed_at,
                error_message=video.error_message,
                summary=summary,
            )
        )

    return VideoListResponse(
        videos=video_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/api/videos/{video_id}", response_model=VideoResponse, tags=["Videos"])
async def get_video(video_id: str, db: Session = Depends(get_db)):
    """
    Get details for a specific video.
    """
    video = db.query(ProcessedVideo).filter(
        ProcessedVideo.video_id == video_id
    ).first()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    channel = db.query(Channel).filter(
        Channel.channel_id == video.channel_id
    ).first()

    summary = None
    if video.summary:
        summary = VideoSummaryResponse(
            core_message=video.summary.get("core_message", ""),
            detailed_summary=video.summary.get("detailed_summary", ""),
            key_takeaways=video.summary.get("key_takeaways", []),
            timestamps=video.summary.get("timestamps", []),
            action_items=video.summary.get("action_items", []),
        )

    return VideoResponse(
        video_id=video.video_id,
        title=video.title,
        description=video.description,
        channel_id=video.channel_id,
        channel_name=channel.channel_name if channel else None,
        duration_seconds=video.duration_seconds,
        published_at=video.published_at,
        thumbnail_url=video.thumbnail_url,
        category=video.category,
        processing_status=video.processing_status,
        processed_at=video.processed_at,
        error_message=video.error_message,
        summary=summary,
    )


# ============================================================================
# Digests
# ============================================================================


@router.get("/api/digests", response_model=DigestListResponse, tags=["Digests"])
async def list_digests(db: Session = Depends(get_db)):
    """
    List digest history.
    """
    digests = db.query(DigestHistory).order_by(
        DigestHistory.sent_at.desc()
    ).all()

    return DigestListResponse(
        digests=[
            DigestResponse(
                id=d.id,
                sent_at=d.sent_at,
                period_start=d.period_start,
                period_end=d.period_end,
                video_count=d.video_count,
                total_duration_seconds=d.total_duration_seconds,
                category_counts=d.category_counts or {},
                email_status=d.email_status,
                trigger_reason=d.trigger_reason,
            )
            for d in digests
        ],
        total=len(digests),
    )


@router.post("/api/trigger-digest", response_model=TriggerDigestResponse, tags=["Digests"])
async def trigger_digest(
    request: TriggerDigestRequest = None,
    db: Session = Depends(get_db),
):
    """
    Manually trigger digest generation.
    """
    # Check if there are videos to include
    pending_count = db.query(ProcessedVideo).filter(
        and_(
            ProcessedVideo.processing_status == "completed",
            ProcessedVideo.included_in_digest_id.is_(None)
        )
    ).count()

    if pending_count == 0 and not (request and request.force):
        return TriggerDigestResponse(
            task_id="",
            status="skipped",
            message="No new videos to include in digest",
        )

    # Queue task
    task = generate_and_send_digest.delay(trigger_reason="manual")

    logger.info(f"Manual digest triggered, task_id: {task.id}")

    return TriggerDigestResponse(
        task_id=task.id,
        status="queued",
        message=f"Digest generation queued with {pending_count} videos",
    )


# ============================================================================
# Tasks
# ============================================================================


@router.get("/api/tasks/{task_id}", response_model=TaskStatusResponse, tags=["Tasks"])
async def get_task_status(task_id: str):
    """
    Get status of a Celery task.
    """
    result = celery_app.AsyncResult(task_id)

    response = TaskStatusResponse(
        task_id=task_id,
        status=result.status,
    )

    if result.ready():
        if result.successful():
            response.result = result.result
        else:
            response.error = str(result.result)

    return response


# ============================================================================
# OAuth
# ============================================================================


@router.get("/api/oauth/status", tags=["OAuth"])
async def get_oauth_status(db: Session = Depends(get_db)):
    """
    Get YouTube OAuth token status.
    """
    token = db.query(OAuthToken).filter(
        OAuthToken.service == "youtube"
    ).first()

    if not token:
        return {
            "valid": False,
            "message": "No OAuth token found. Run auth flow.",
        }

    return {
        "valid": not token.is_expired,
        "expires_at": token.expires_at,
        "last_refreshed": token.last_refreshed,
    }
