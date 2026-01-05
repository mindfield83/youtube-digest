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
from app.tasks import generate_and_send_digest, sync_channel_metadata

logger = logging.getLogger(__name__)

router = APIRouter()
health_router = APIRouter()  # Separate router for health check (no auth)


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


@health_router.get("/health", response_model=HealthResponse, tags=["Health"])
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

    Reads token from file system and attempts refresh if needed.
    """
    from pathlib import Path
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    token_path = Path("credentials/youtube_token.json")
    SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

    try:
        if not token_path.exists():
            return {
                "valid": False,
                "message": "Kein OAuth-Token gefunden. OAuth-Flow ausführen.",
            }

        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        # Try to refresh if expired
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Token is now valid after refresh
            except Exception as refresh_error:
                logger.warning(f"Token refresh failed: {refresh_error}")

        # Token is usable if valid OR has refresh_token (can be refreshed on demand)
        is_usable = creds.valid or creds.refresh_token is not None

        return {
            "valid": is_usable,
            "expired": creds.expired,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            "has_refresh_token": creds.refresh_token is not None,
            "can_refresh": creds.refresh_token is not None,
        }
    except Exception as e:
        logger.error(f"Error checking OAuth status: {e}")
        return {
            "valid": False,
            "message": f"Fehler beim Token-Check: {str(e)}",
        }


# ============================================================================
# HTML Partials for HTMX Dashboard (German)
# ============================================================================


@router.get("/api/status", response_class=HTMLResponse, tags=["HTMX Partials"])
async def get_status_html(db: Session = Depends(get_db)):
    """Return status bar as HTML partial for HTMX - compact two-line layout."""
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

    # OAuth status - check if token exists and has refresh capability
    try:
        from pathlib import Path
        from google.oauth2.credentials import Credentials
        token_path = Path("credentials/youtube_token.json")
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(token_path),
                ["https://www.googleapis.com/auth/youtube.readonly"]
            )
            # Valid if token works OR has refresh_token (can be refreshed on demand)
            oauth_valid = creds.valid or creds.refresh_token is not None
        else:
            oauth_valid = False
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

    # Status dot classes
    oauth_dot = "ok" if oauth_valid else "error"
    oauth_text = "Verbunden" if oauth_valid else "Nicht verbunden"
    worker_dot = "ok" if worker_active else "error"
    worker_text = "Aktiv" if worker_active else "Inaktiv"

    # Format timestamps
    last_check_text = _format_date(last_check) if last_check else "Noch nie"
    last_digest_text = _format_date(last_digest.sent_at) if last_digest else "Noch keiner"

    # Total videos
    total_videos = pending + processing + completed + failed

    # Status dot colors
    oauth_color = "#22c55e" if oauth_valid else "#ef4444"
    worker_color = "#22c55e" if worker_active else "#ef4444"

    html = f"""
    <div class="status-bar" style="background: #fff; border-radius: 8px; padding: 16px 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); display: flex; flex-wrap: wrap; gap: 12px 32px; align-items: center;">
        <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span style="width: 10px; height: 10px; border-radius: 50%; background: {oauth_color};"></span>
            <span style="color: #666; font-size: 0.85rem;">OAuth</span>
            <strong style="color: #17214B;">{oauth_text}</strong>
        </span>
        <span style="color: #ddd;">|</span>
        <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span style="width: 10px; height: 10px; border-radius: 50%; background: {worker_color};"></span>
            <span style="color: #666; font-size: 0.85rem;">Worker</span>
            <strong style="color: #17214B;">{worker_text}</strong>
        </span>
        <span style="color: #ddd;">|</span>
        <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span style="color: #666; font-size: 0.85rem;">Kanäle</span>
            <strong style="color: #17214B;">{total_channels}</strong>
        </span>
        <span style="color: #ddd;">|</span>
        <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span style="color: #666; font-size: 0.85rem;">Videos</span>
            <strong style="color: #17214B;" title="Ausstehend: {pending}, In Bearbeitung: {processing}, Abgeschlossen: {completed}, Fehlgeschlagen: {failed}">{total_videos}</strong>
            <span style="color: #999; font-size: 0.8rem;">(⏳{pending} ⚙️{processing} ✅{completed} ❌{failed})</span>
        </span>
        <span style="color: #ddd;">|</span>
        <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span style="color: #666; font-size: 0.85rem;">Letzte Prüfung</span>
            <strong style="color: #17214B;">{last_check_text}</strong>
        </span>
        <span style="color: #ddd;">|</span>
        <span style="display: inline-flex; align-items: center; gap: 6px;">
            <span style="color: #666; font-size: 0.85rem;">Letzter Digest</span>
            <strong style="color: #17214B;">{last_digest_text}</strong>
        </span>
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
        "pending": ("⏳", "Ausstehend", "pending"),
        "processing": ("⚙️", "In Bearbeitung", "processing"),
        "completed": ("✅", "Abgeschlossen", "completed"),
        "failed": ("❌", "Fehlgeschlagen", "failed"),
    }

    cards = []
    for v in videos:
        channel_name = channel_map.get(v.channel_id, "Unbekannt")
        status_icon, status_text, status_class = status_labels.get(
            v.processing_status, ("❓", v.processing_status, "")
        )
        duration = _format_duration(v.duration_seconds)
        pub_date = v.published_at.strftime("%d.%m.%Y") if v.published_at else "-"
        category_display = v.category or "Nicht kategorisiert"

        # Get thumbnail URL or use placeholder
        thumbnail = v.thumbnail_url or f"https://i.ytimg.com/vi/{v.video_id}/mqdefault.jpg"

        # Extract summary preview if available (larger: 300 chars, 4 takeaways)
        summary_html = ""
        if v.summary and v.processing_status == "completed":
            core_message = v.summary.get("core_message", "")
            key_takeaways = v.summary.get("key_takeaways", [])[:4]  # 4 instead of 2

            if core_message:
                # Truncate core message (300 chars instead of 150)
                core_preview = core_message[:300] + "..." if len(core_message) > 300 else core_message
                summary_html = f'<p>{core_preview}</p>'

                if key_takeaways:
                    # 120 chars per takeaway instead of 80
                    takeaways_html = "".join(f"<li>{t[:120]}{'...' if len(t) > 120 else ''}</li>" for t in key_takeaways)
                    summary_html += f'<ul>{takeaways_html}</ul>'

        cards.append(f"""
        <article class="video-card">
            <div class="video-card__thumbnail">
                <img src="{thumbnail}" alt="" loading="lazy">
                <span class="video-card__duration">{duration}</span>
            </div>
            <div class="video-card__content">
                <h3 class="video-card__title">
                    <a href="https://youtube.com/watch?v={v.video_id}" target="_blank">{v.title}</a>
                </h3>
                <div class="video-card__meta">{channel_name} | {pub_date}</div>
                {f'<div class="video-card__summary">{summary_html}</div>' if summary_html else ''}
                <div class="video-card__badges">
                    <span class="category-badge">{category_display}</span>
                    <span class="status-badge {status_class}">{status_icon} {status_text}</span>
                </div>
            </div>
        </article>
        """)

    total_pages = (total + page_size - 1) // page_size

    # Build pagination with Prev/Next buttons
    # Query params for HTMX target
    base_params = []
    if category:
        base_params.append(f"category={category}")
    if status:
        base_params.append(f"status={status}")
    base_params.append(f"page_size={page_size}")
    base_query = "&".join(base_params)

    prev_disabled = "disabled" if page <= 1 else ""
    next_disabled = "disabled" if page >= total_pages else ""
    prev_page = max(1, page - 1)
    next_page = min(total_pages, page + 1)

    pagination_html = f"""
    <div class="pagination-nav">
        <button class="pagination-btn {prev_disabled}"
                hx-get="/api/videos?{base_query}&page={prev_page}"
                hx-target="#videos-list"
                hx-swap="innerHTML"
                {'disabled' if page <= 1 else ''}>
            <i class="ph ph-caret-left"></i> Vorherige
        </button>
        <span class="pagination-info">Seite {page} von {total_pages}</span>
        <button class="pagination-btn {next_disabled}"
                hx-get="/api/videos?{base_query}&page={next_page}"
                hx-target="#videos-list"
                hx-swap="innerHTML"
                {'disabled' if page >= total_pages else ''}>
            Nächste <i class="ph ph-caret-right"></i>
        </button>
    </div>
    """

    html = f"""
    <div class="table-info">Zeige {len(videos)} von {total} Videos</div>
    <div class="video-cards">
        {''.join(cards)}
    </div>
    {pagination_html}
    """
    return HTMLResponse(content=html)


@router.get("/api/channels", response_class=HTMLResponse, tags=["HTMX Partials"])
async def get_channels_html(db: Session = Depends(get_db)):
    """Return channels list as HTML partial for HTMX."""
    from app.config import get_settings

    channels = db.query(Channel).filter(Channel.is_active == True).order_by(Channel.channel_name).all()

    if not channels:
        return HTMLResponse(content='<div class="empty-state">Keine Kanäle gefunden</div>')

    settings = get_settings()
    categories = settings.categories

    rows = []
    for c in channels:
        video_count = db.query(ProcessedVideo).filter(
            ProcessedVideo.channel_id == c.channel_id
        ).count()
        last_checked = _format_date(c.last_checked) if c.last_checked else "Nie"
        thumb = f'<img src="{c.thumbnail_url}" alt="" class="channel-thumb">' if c.thumbnail_url else ""

        # Build category dropdown options
        options = ['<option value="">-- Keine --</option>']
        for cat in categories:
            selected = 'selected' if c.manual_category == cat else ''
            options.append(f'<option value="{cat}" {selected}>{cat}</option>')
        options_html = ''.join(options)

        # Category dropdown with HTMX
        category_dropdown = f"""
        <select class="category-select"
                hx-post="/api/channels/{c.channel_id}/category"
                hx-vals="js:{{category: event.target.value}}"
                hx-swap="none"
                hx-on::after-request="handleCategoryChange(event)">
            {options_html}
        </select>
        """

        rows.append(f"""
        <tr>
            <td>{thumb}</td>
            <td>
                <a href="{c.channel_url}" target="_blank">{c.channel_name}</a>
            </td>
            <td>{video_count}</td>
            <td>{category_dropdown}</td>
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


@router.post("/api/digests/{digest_id}/resend", tags=["Digests"])
async def resend_digest(digest_id: int, db: Session = Depends(get_db)):
    """
    Resend an existing digest email.

    Regenerates the digest HTML using the videos that were included
    in the original digest and sends it again.
    """
    from app.services.digest_generator import DigestGenerator
    from app.services.email_service import EmailService

    # Find the digest
    digest = db.query(DigestHistory).filter(DigestHistory.id == digest_id).first()
    if not digest:
        raise HTTPException(status_code=404, detail="Digest nicht gefunden")

    # Get videos that were included in this digest
    videos = db.query(ProcessedVideo).filter(
        ProcessedVideo.included_in_digest_id == digest_id
    ).order_by(ProcessedVideo.published_at.desc()).all()

    if not videos:
        raise HTTPException(
            status_code=400,
            detail="Keine Videos für diesen Digest gefunden"
        )

    # Build channel map
    channel_ids = list(set(v.channel_id for v in videos))
    channels = db.query(Channel).filter(Channel.channel_id.in_(channel_ids)).all()
    channel_map = {c.channel_id: c.channel_name for c in channels}

    try:
        # Generate digest HTML
        digest_gen = DigestGenerator()
        html_content, plain_content = digest_gen.generate(videos, channel_map)

        # Send email
        email_service = EmailService()
        result = email_service.send_digest(
            html_content=html_content,
            plain_content=plain_content,
            subject=f"YouTube Digest - {len(videos)} Videos (erneut gesendet)",
        )

        if result.success:
            logger.info(f"Digest {digest_id} resent successfully")
            return {
                "success": True,
                "message": f"Digest mit {len(videos)} Videos erneut gesendet",
            }
        else:
            logger.error(f"Resend failed for digest {digest_id}: {result.message}")
            return {
                "success": False,
                "message": f"E-Mail-Versand fehlgeschlagen: {result.message}",
            }
    except Exception as e:
        logger.error(f"Error resending digest {digest_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Fehler: {str(e)}")


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

        # Resend button with HTMX
        resend_btn = f"""
        <button class="btn-resend"
                hx-post="/api/digests/{d.id}/resend"
                hx-swap="none"
                hx-on::after-request="handleResendResponse(event)"
                title="Digest erneut senden">
            <i class="ph ph-paper-plane-tilt"></i>
        </button>
        """

        rows.append(f"""
        <tr>
            <td>{sent_date}</td>
            <td>{period}</td>
            <td>{d.video_count}</td>
            <td>{duration}</td>
            <td>{trigger}</td>
            <td title="{status_text}">{status_icon}</td>
            <td>{resend_btn}</td>
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
                <th></th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """
    return HTMLResponse(content=html)


# ============================================================================
# Channel Sync & Test Email
# ============================================================================


@router.post("/api/channels/{channel_id}/category", tags=["Channels"])
async def set_channel_category(
    channel_id: str,
    category: Optional[str] = Query(None, description="Category name or empty to clear"),
    db: Session = Depends(get_db),
):
    """
    Set or clear manual category override for a channel.

    When set, videos from this channel will use the manual category
    instead of AI-based categorization.
    """
    from app.config import get_settings

    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    settings = get_settings()

    # Validate category if provided
    if category and category not in settings.categories:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Valid options: {', '.join(settings.categories)}"
        )

    # Set or clear category
    channel.manual_category = category if category else None
    db.commit()

    action = f"auf '{category}' gesetzt" if category else "entfernt"
    logger.info(f"Manual category for {channel.channel_name} {action}")

    return {
        "success": True,
        "channel_id": channel_id,
        "manual_category": channel.manual_category,
        "message": f"Kategorie {action}",
    }


@router.post("/api/channels/sync", tags=["Channels"])
async def sync_channels():
    """
    Trigger channel metadata and video sync from YouTube API.

    Updates channel names, thumbnails, and fetches new videos
    (last 14 days or up to 10 videos per channel).
    """
    task = sync_channel_metadata.delay(fetch_videos=True)

    logger.info(f"Channel & video sync triggered, task_id: {task.id}")

    return {
        "task_id": task.id,
        "status": "queued",
        "message": "Kanäle & Videos werden synchronisiert",
    }


@router.post("/api/test-email", tags=["Email"])
async def send_test_email():
    """
    Send a test email to verify Resend configuration.
    """
    from app.services.email_service import EmailService

    try:
        email_service = EmailService()
        result = email_service.send_digest(
            html_content="<h1>Test-E-Mail</h1><p>Dies ist eine Test-E-Mail von YouTube Digest.</p>",
            plain_content="Test-E-Mail\n\nDies ist eine Test-E-Mail von YouTube Digest.",
            subject="YouTube Digest - Test-E-Mail",
        )

        if result.success:
            return {
                "success": True,
                "message": "Test-E-Mail erfolgreich gesendet",
            }
        else:
            return {
                "success": False,
                "message": f"E-Mail-Versand fehlgeschlagen: {result.message}",
            }
    except Exception as e:
        logger.error(f"Test email failed: {e}")
        return {
            "success": False,
            "message": f"Fehler: {str(e)}",
        }


# ============================================================================
# Task Progress Tracking
# ============================================================================


@router.get("/api/tasks/{task_id}/progress", response_class=HTMLResponse, tags=["Tasks"])
async def get_task_progress_html(task_id: str):
    """
    Get task progress as HTML partial for HTMX polling.

    Returns progress modal content with current phase, progress bar, and status.
    """
    result = celery_app.AsyncResult(task_id)

    status = result.status
    meta = result.info if isinstance(result.info, dict) else {}

    # Handle different states
    if status == "PENDING":
        phase = "initializing"
        percent = 0
        message = "Task wird vorbereitet..."
        current_channel = None
        current_video = None
    elif status == "PROGRESS":
        phase = meta.get("phase", "processing")
        percent = meta.get("percent", 0)
        message = meta.get("message", "Verarbeitung läuft...")
        current_channel = meta.get("current_channel")
        current_video = meta.get("current_video_title")
    elif status == "SUCCESS":
        phase = "completed"
        percent = 100
        message = "Digest erfolgreich erstellt und gesendet!"
        current_channel = None
        current_video = None
    elif status == "FAILURE":
        phase = "failed"
        percent = 100
        message = f"Fehler: {str(result.result)}"
        current_channel = None
        current_video = None
    else:
        phase = status.lower()
        percent = meta.get("percent", 0) if isinstance(meta, dict) else 0
        message = meta.get("message", f"Status: {status}") if isinstance(meta, dict) else f"Status: {status}"
        current_channel = meta.get("current_channel") if isinstance(meta, dict) else None
        current_video = meta.get("current_video_title") if isinstance(meta, dict) else None

    # Phase icons
    phase_icons = {
        "initializing": "ph-hourglass",
        "processing": "ph-gear",
        "generating_digest": "ph-file-text",
        "sending_email": "ph-envelope",
        "completed": "ph-check-circle",
        "failed": "ph-x-circle",
    }
    icon = phase_icons.get(phase, "ph-spinner")

    # Phase colors
    phase_colors = {
        "completed": "#22c55e",
        "failed": "#ef4444",
    }
    color = phase_colors.get(phase, "#17214B")

    # Build HTML
    channel_html = f'<div class="progress-channel">{current_channel}</div>' if current_channel else ""
    video_html = f'<div class="progress-video">{current_video}</div>' if current_video else ""

    # Auto-close attribute for completed/failed states
    auto_close = 'data-auto-close="5000"' if phase in ("completed", "failed") else ""

    html = f"""
    <div class="progress-content" {auto_close}>
        <div class="progress-icon" style="color: {color};">
            <i class="{icon}"></i>
        </div>
        <div class="progress-info">
            <div class="progress-message">{message}</div>
            {channel_html}
            {video_html}
        </div>
        <div class="progress-bar-container">
            <div class="progress-bar" style="width: {percent}%;"></div>
        </div>
        <div class="progress-percent">{percent}%</div>
    </div>
    """

    return HTMLResponse(content=html)
