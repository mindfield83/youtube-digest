# app/api/routes.py
"""API route handlers for YouTube Digest dashboard."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
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
# HTML Partial Helpers (for HTMX)
# ============================================================================


def _format_duration(seconds: Optional[int]) -> str:
    """Format seconds to HH:MM:SS or MM:SS."""
    if not seconds:
        return "0:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_date(dt: Optional[datetime]) -> str:
    """Format datetime to readable string."""
    if not dt:
        return "-"
    return dt.strftime("%d.%m.%Y %H:%M")


# ============================================================================
# Health Check
# ============================================================================


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint for Docker/Kubernetes.

    Checks database and Redis connectivity.
    Returns 503 if unhealthy.
    """
    import redis as redis_lib
    from app import __version__
    from app.config import get_settings

    health_status = {
        "status": "healthy",
        "database": "unknown",
        "redis": "unknown",
        "version": __version__,
    }

    # Check database
    try:
        db.execute(text("SELECT 1"))
        health_status["database"] = "connected"
    except Exception as e:
        health_status["database"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    # Check Redis
    try:
        settings = get_settings()
        r = redis_lib.from_url(settings.redis_url)
        r.ping()
        health_status["redis"] = "connected"
    except Exception as e:
        health_status["redis"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    if health_status["status"] == "unhealthy":
        raise HTTPException(status_code=503, detail=health_status)

    return health_status






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
async def get_oauth_status():
    """
    Get YouTube OAuth token status.

    Reads token from file system (not database) since YouTubeService
    uses file-based token storage.
    """
    from app.services.youtube_service import YouTubeService

    try:
        service = YouTubeService()
        creds = service._load_credentials()

        if not creds:
            return {
                "valid": False,
                "message": "No OAuth token found. Run auth flow.",
            }

        # Token is valid if it has a refresh_token (can be refreshed) OR is not expired
        is_valid = creds.refresh_token is not None or (creds.valid and not creds.expired)

        return {
            "valid": is_valid,
            "expired": creds.expired,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            "has_refresh_token": creds.refresh_token is not None,
            "can_refresh": creds.refresh_token is not None,
        }
    except Exception as e:
        logger.error(f"Error checking OAuth status: {e}")
        return {
            "valid": False,
            "message": f"Error checking token: {str(e)}",
        }


# ============================================================================
# HTML Partials for HTMX Dashboard (German)
# ============================================================================


@router.get("/api/status", response_class=HTMLResponse, tags=["HTMX Partials"])
async def get_status_html(db: Session = Depends(get_db)):
    """Return status cards as HTML partial for HTMX."""
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

    # Last check
    last_check = db.query(func.max(Channel.last_checked)).scalar()

    # Last digest
    last_digest = db.query(DigestHistory).filter(
        DigestHistory.email_status == "sent"
    ).order_by(DigestHistory.sent_at.desc()).first()

    # OAuth status
    try:
        from app.services.youtube_service import YouTubeService
        service = YouTubeService()
        creds = service._load_credentials()
        oauth_valid = creds is not None and (
            creds.refresh_token is not None or (creds.valid and not creds.expired)
        )
    except Exception:
        oauth_valid = False

    # Total channels
    total_channels = db.query(Channel).filter(Channel.is_active == True).count()

    # Worker status
    try:
        inspect = celery_app.control.inspect()
        active = inspect.active()
        worker_active = active is not None and len(active) > 0
    except Exception:
        worker_active = False

    oauth_class = "status-ok" if oauth_valid else "status-error"
    oauth_text = "Verbunden" if oauth_valid else "Nicht verbunden"
    worker_class = "status-ok" if worker_active else "status-error"
    worker_text = "Aktiv" if worker_active else "Inaktiv"

    html = f"""
    <div class="status-cards">
        <div class="status-card">
            <h3>YouTube OAuth</h3>
            <span class="status-badge {oauth_class}">{oauth_text}</span>
        </div>
        <div class="status-card">
            <h3>Worker</h3>
            <span class="status-badge {worker_class}">{worker_text}</span>
        </div>
        <div class="status-card">
            <h3>Kanäle</h3>
            <span class="status-value">{total_channels}</span>
        </div>
        <div class="status-card">
            <h3>Videos</h3>
            <div class="status-breakdown">
                <span title="Ausstehend">⏳ {pending}</span>
                <span title="In Bearbeitung">⚙️ {processing}</span>
                <span title="Abgeschlossen">✅ {completed}</span>
                <span title="Fehlgeschlagen">❌ {failed}</span>
            </div>
        </div>
        <div class="status-card">
            <h3>Letzte Prüfung</h3>
            <span class="status-value">{_format_date(last_check)}</span>
        </div>
        <div class="status-card">
            <h3>Letzter Digest</h3>
            <span class="status-value">{_format_date(last_digest.sent_at) if last_digest else 'Noch keiner'}</span>
        </div>
    </div>
    """
    return HTMLResponse(content=html)


@router.get("/api/videos", response_class=HTMLResponse, tags=["HTMX Partials"])
async def get_videos_html(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return videos list as HTML partial for HTMX."""
    query = db.query(ProcessedVideo)

    if category:
        query = query.filter(ProcessedVideo.category == category)
    if status:
        query = query.filter(ProcessedVideo.processing_status == status)

    total = query.count()
    offset = (page - 1) * page_size
    videos = query.order_by(ProcessedVideo.published_at.desc()).offset(offset).limit(page_size).all()

    if not videos:
        return HTMLResponse(content='<div class="empty-state">Keine Videos gefunden</div>')

    # Build channel name cache
    channel_ids = list(set(v.channel_id for v in videos))
    channels = db.query(Channel).filter(Channel.channel_id.in_(channel_ids)).all()
    channel_map = {c.channel_id: c.channel_name for c in channels}

    # Status translations
    status_labels = {
        "pending": ("⏳", "Ausstehend"),
        "processing": ("⚙️", "In Bearbeitung"),
        "completed": ("✅", "Abgeschlossen"),
        "failed": ("❌", "Fehlgeschlagen"),
    }

    rows = []
    for v in videos:
        channel_name = channel_map.get(v.channel_id, "Unbekannt")
        status_icon, status_text = status_labels.get(v.processing_status, ("❓", v.processing_status))
        duration = _format_duration(v.duration_seconds)
        pub_date = v.published_at.strftime("%d.%m.%Y") if v.published_at else "-"
        category_display = v.category or "Nicht kategorisiert"

        rows.append(f"""
        <tr>
            <td>
                <a href="https://youtube.com/watch?v={v.video_id}" target="_blank" title="{v.title}">
                    {v.title[:50]}{'...' if len(v.title) > 50 else ''}
                </a>
            </td>
            <td>{channel_name}</td>
            <td>{duration}</td>
            <td>{pub_date}</td>
            <td><span class="category-badge">{category_display}</span></td>
            <td title="{status_text}">{status_icon} {status_text}</td>
        </tr>
        """)

    total_pages = (total + page_size - 1) // page_size

    html = f"""
    <div class="table-info">Zeige {len(videos)} von {total} Videos</div>
    <table class="data-table">
        <thead>
            <tr>
                <th>Titel</th>
                <th>Kanal</th>
                <th>Dauer</th>
                <th>Veröffentlicht</th>
                <th>Kategorie</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    <div class="pagination">
        Seite {page} von {total_pages}
    </div>
    """
    return HTMLResponse(content=html)


@router.get("/api/channels", response_class=HTMLResponse, tags=["HTMX Partials"])
async def get_channels_html(db: Session = Depends(get_db)):
    """Return channels list as HTML partial for HTMX."""
    channels = db.query(Channel).filter(Channel.is_active == True).order_by(Channel.channel_name).all()

    if not channels:
        return HTMLResponse(content='<div class="empty-state">Keine Kanäle gefunden</div>')

    rows = []
    for c in channels:
        video_count = db.query(ProcessedVideo).filter(
            ProcessedVideo.channel_id == c.channel_id
        ).count()
        last_checked = _format_date(c.last_checked) if c.last_checked else "Nie"
        thumb = f'<img src="{c.thumbnail_url}" alt="" class="channel-thumb">' if c.thumbnail_url else ""

        rows.append(f"""
        <tr>
            <td>{thumb}</td>
            <td>
                <a href="{c.channel_url}" target="_blank">{c.channel_name}</a>
            </td>
            <td>{video_count}</td>
            <td>{c.manual_category or '-'}</td>
            <td>{last_checked}</td>
        </tr>
        """)

    html = f"""
    <div class="table-info">{len(channels)} aktive Kanäle</div>
    <table class="data-table">
        <thead>
            <tr>
                <th></th>
                <th>Kanal</th>
                <th>Videos</th>
                <th>Manuelle Kategorie</th>
                <th>Zuletzt geprüft</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """
    return HTMLResponse(content=html)


@router.get("/api/digests", response_class=HTMLResponse, tags=["HTMX Partials"])
async def get_digests_html(db: Session = Depends(get_db)):
    """Return digest history as HTML partial for HTMX."""
    digests = db.query(DigestHistory).order_by(DigestHistory.sent_at.desc()).all()

    if not digests:
        return HTMLResponse(content='<div class="empty-state">Noch keine Digests versendet</div>')

    # Trigger reason translations
    trigger_labels = {
        "scheduled": "Geplant",
        "manual": "Manuell",
        "video_threshold": "Video-Schwellenwert",
        "time_threshold": "Zeit-Schwellenwert",
    }

    status_labels = {
        "sent": ("✅", "Gesendet"),
        "pending": ("⏳", "Ausstehend"),
        "failed": ("❌", "Fehlgeschlagen"),
    }

    rows = []
    for d in digests:
        sent_date = _format_date(d.sent_at)
        period = f"{d.period_start.strftime('%d.%m.')} - {d.period_end.strftime('%d.%m.%Y')}" if d.period_start and d.period_end else "-"
        duration = _format_duration(d.total_duration_seconds)
        trigger = trigger_labels.get(d.trigger_reason, d.trigger_reason or "-")
        status_icon, status_text = status_labels.get(d.email_status, ("❓", d.email_status))

        rows.append(f"""
        <tr>
            <td>{sent_date}</td>
            <td>{period}</td>
            <td>{d.video_count}</td>
            <td>{duration}</td>
            <td>{trigger}</td>
            <td title="{status_text}">{status_icon}</td>
        </tr>
        """)

    html = f"""
    <div class="table-info">{len(digests)} Digests</div>
    <table class="data-table">
        <thead>
            <tr>
                <th>Gesendet</th>
                <th>Zeitraum</th>
                <th>Videos</th>
                <th>Gesamtdauer</th>
                <th>Auslöser</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """
    return HTMLResponse(content=html)
