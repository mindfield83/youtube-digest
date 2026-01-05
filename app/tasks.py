# app/tasks.py
"""Celery tasks for YouTube Digest workflow."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_

from app.celery_app import celery_app
from app.config import get_settings
from app.models import (
    Channel,
    DigestHistory,
    ProcessedVideo,
    SessionLocal,
)
from app.services.digest_generator import DigestGenerator
from app.services.email_service import EmailService
from app.services.summarization_service import SummarizationService
from app.services.transcript_service import TranscriptService
from app.services.youtube_service import YouTubeService

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(bind=True, name="app.tasks.check_for_new_videos")
def check_for_new_videos(self) -> dict[str, Any]:
    """
    Check all subscribed channels for new videos.

    Runs daily via Celery Beat. Queues process_video task for each new video.

    Returns:
        dict with status, channels_checked, new_videos_found
    """
    logger.info("Starting check for new videos")

    try:
        youtube = YouTubeService()
        subscriptions = youtube.get_subscriptions()

        channels_checked = 0
        new_videos_found = 0
        videos_queued = 0

        with SessionLocal() as db:
            # Calculate date range (check last 2 days to handle timezone issues)
            since_date = datetime.now(timezone.utc) - timedelta(days=2)

            for sub in subscriptions:
                channel_id = sub["channel_id"]
                channel_name = sub.get("channel_name", "Unknown")

                # Upsert channel in database
                channel = db.query(Channel).filter(
                    Channel.channel_id == channel_id
                ).first()

                if not channel:
                    channel = Channel(
                        channel_id=channel_id,
                        channel_name=channel_name,
                        channel_url=f"https://www.youtube.com/channel/{channel_id}",
                        thumbnail_url=sub.get("thumbnail_url"),
                        is_active=True,
                    )
                    db.add(channel)
                    db.flush()

                # Get new videos from channel
                try:
                    videos = youtube.get_channel_videos(channel_id, since_date)
                    channels_checked += 1

                    for video in videos:
                        video_id = video["video_id"]

                        # Check if already processed
                        existing = db.query(ProcessedVideo).filter(
                            ProcessedVideo.video_id == video_id
                        ).first()

                        if existing:
                            continue

                        # Create pending video record
                        new_video = ProcessedVideo(
                            video_id=video_id,
                            channel_id=channel_id,
                            title=video.get("title", ""),
                            description=video.get("description", ""),
                            duration_seconds=video.get("duration_seconds", 0),
                            published_at=video.get("published_at", datetime.now(timezone.utc)),
                            thumbnail_url=video.get("thumbnail"),
                            processing_status="pending",
                        )
                        db.add(new_video)
                        db.flush()

                        new_videos_found += 1

                        # Queue for processing
                        process_video.delay(video_id)
                        videos_queued += 1

                except Exception as e:
                    logger.warning(f"Error fetching videos for {channel_id}: {e}")
                    continue

            # Update last_checked for all active channels
            db.query(Channel).filter(Channel.is_active == True).update(
                {"last_checked": datetime.now(timezone.utc)}
            )
            db.commit()

        # Check if threshold reached for digest
        with SessionLocal() as db:
            pending_digest_count = db.query(ProcessedVideo).filter(
                and_(
                    ProcessedVideo.processing_status == "completed",
                    ProcessedVideo.included_in_digest_id.is_(None)
                )
            ).count()

            if pending_digest_count >= settings.digest_video_threshold:
                logger.info(
                    f"Video threshold ({settings.digest_video_threshold}) reached, "
                    f"triggering digest generation"
                )
                generate_and_send_digest.delay()

        result = {
            "status": "completed",
            "channels_checked": channels_checked,
            "new_videos_found": new_videos_found,
            "videos_queued": videos_queued,
        }
        logger.info(f"Check complete: {result}")
        return result

    except Exception as e:
        logger.error(f"Error in check_for_new_videos: {e}")
        raise self.retry(exc=e, countdown=300, max_retries=3)


@celery_app.task(bind=True, name="app.tasks.process_video")
def process_video(self, video_id: str) -> dict[str, Any]:
    """
    Process a single video: fetch transcript, summarize, store.

    Args:
        video_id: YouTube video ID

    Returns:
        dict with status, video_id, category
    """
    logger.info(f"Processing video: {video_id}")

    try:
        with SessionLocal() as db:
            video = db.query(ProcessedVideo).filter(
                ProcessedVideo.video_id == video_id
            ).first()

            if not video:
                return {"status": "error", "message": f"Video {video_id} not found"}

            if video.processing_status == "completed":
                return {"status": "skipped", "message": "Already processed"}

            # Update status to processing
            video.processing_status = "processing"
            db.commit()

            try:
                # Fetch transcript
                transcript_service = TranscriptService()
                transcript_result = transcript_service.get_transcript(video_id)

                video.transcript = transcript_result.text
                video.transcript_source = transcript_result.source

                # Get channel name for summarization
                channel = db.query(Channel).filter(
                    Channel.channel_id == video.channel_id
                ).first()
                channel_name = channel.channel_name if channel else "Unknown"

                # Summarize
                summarization_service = SummarizationService()
                summary = summarization_service.summarize_video(
                    transcript=transcript_result.text,
                    title=video.title,
                    channel=channel_name,
                    duration_seconds=video.duration_seconds,
                )

                # Store summary as dict
                video.category = summary.category.value
                video.summary = {
                    "core_message": summary.core_message,
                    "detailed_summary": summary.detailed_summary,
                    "key_takeaways": summary.key_takeaways,
                    "timestamps": [
                        {"time": ts.time, "description": ts.description}
                        for ts in summary.timestamps
                    ],
                    "action_items": summary.action_items,
                }
                video.processing_status = "completed"
                video.processed_at = datetime.now(timezone.utc)
                video.error_message = None
                db.commit()

                logger.info(f"Video {video_id} processed successfully: {summary.category.value}")

                return {
                    "status": "completed",
                    "video_id": video_id,
                    "category": summary.category.value,
                }

            except Exception as e:
                video.processing_status = "failed"
                video.error_message = str(e)
                video.retry_count = (video.retry_count or 0) + 1
                video.last_retry_at = datetime.now(timezone.utc)
                db.commit()
                raise

    except Exception as e:
        logger.error(f"Error processing video {video_id}: {e}")

        # Retry with exponential backoff
        countdown = 60 * (2 ** self.request.retries)  # 1min, 2min, 4min
        raise self.retry(exc=e, countdown=countdown, max_retries=3)


# ============================================================================
# Helper functions for combined digest workflow
# ============================================================================


def _sync_channels_and_fetch_videos(task, update_state_fn) -> list[str]:
    """
    Sync channel metadata and fetch new videos (inline, not as separate task).

    Args:
        task: The Celery task instance (unused, kept for compatibility)
        update_state_fn: Function to update task state

    Returns:
        List of new video IDs that need processing
    """
    youtube = YouTubeService()
    subscriptions = youtube.get_subscriptions()
    new_video_ids = []

    with SessionLocal() as db:
        # Get channel details for descriptions
        channel_ids = [sub["channel_id"] for sub in subscriptions if sub.get("channel_id")]
        channel_details = youtube.get_channel_details(channel_ids)
        details_map = {c["channel_id"]: c for c in channel_details}

        total_channels = len(subscriptions)

        for idx, sub in enumerate(subscriptions):
            channel_id = sub["channel_id"]
            channel_name = sub.get("channel_name", "Unknown")
            thumbnail_url = sub.get("thumbnail_url")
            details = details_map.get(channel_id, {})
            description = details.get("description", "")

            # Update progress
            percent = 5 + int((idx / total_channels) * 10)  # 5-15%
            update_state_fn(
                state="PROGRESS",
                meta={
                    "phase": "sync",
                    "current": idx + 1,
                    "total": total_channels,
                    "percent": percent,
                    "message": f"Prüfe Kanal {idx + 1}/{total_channels}...",
                    "current_channel": channel_name,
                }
            )

            # Upsert channel
            channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()
            if not channel:
                channel = Channel(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    channel_url=f"https://www.youtube.com/channel/{channel_id}",
                    thumbnail_url=thumbnail_url,
                    description=description,
                    is_active=True,
                )
                db.add(channel)
            else:
                channel.channel_name = channel_name
                channel.thumbnail_url = thumbnail_url
                channel.description = description

            # Update last_checked
            channel.last_checked = datetime.now(timezone.utc)

            # Fetch videos (last 14 days, max 10 per channel)
            try:
                since_date = datetime.now(timezone.utc) - timedelta(days=14)
                videos = youtube.get_channel_videos(channel_id, since_date=since_date, max_results=10)

                if videos:
                    video_ids = [v["video_id"] for v in videos if v.get("video_id")]
                    video_details = youtube.get_video_details(video_ids)
                    vdetails_map = {v["video_id"]: v for v in video_details}

                    for video in videos:
                        video_id = video.get("video_id")
                        if not video_id:
                            continue

                        # Skip if already in DB
                        existing = db.query(ProcessedVideo).filter(
                            ProcessedVideo.video_id == video_id
                        ).first()
                        if existing:
                            continue

                        vdetails = vdetails_map.get(video_id, {})

                        # Skip short videos (< 2 min) and livestreams
                        duration = vdetails.get("duration_seconds", 0)
                        if duration < 120:
                            continue
                        if vdetails.get("liveStreamingDetails"):
                            continue

                        # Parse published_at
                        published_at = video.get("published_at")
                        if isinstance(published_at, str):
                            try:
                                published_at = datetime.fromisoformat(
                                    published_at.replace("Z", "+00:00")
                                )
                            except ValueError:
                                published_at = datetime.now(timezone.utc)

                        # Create pending video
                        new_video = ProcessedVideo(
                            video_id=video_id,
                            channel_id=channel_id,
                            title=video.get("title", ""),
                            description=video.get("description", ""),
                            duration_seconds=duration,
                            published_at=published_at,
                            thumbnail_url=vdetails.get("thumbnail_url") or video.get("thumbnail_url"),
                            processing_status="pending",
                        )
                        db.add(new_video)
                        new_video_ids.append(video_id)

            except Exception as e:
                logger.warning(f"Error fetching videos for {channel_name}: {e}")
                continue

        db.commit()

    logger.info(f"Sync complete: {len(new_video_ids)} new videos found")
    return new_video_ids


def _process_videos_sync(task, video_ids: list[str], update_state_fn) -> None:
    """
    Process videos synchronously (transcript + summarization).

    Args:
        task: The Celery task instance (unused, kept for compatibility)
        video_ids: List of video IDs to process
        update_state_fn: Function to update task state
    """
    transcript_service = TranscriptService()
    summarization_service = SummarizationService()

    total = len(video_ids)

    for idx, video_id in enumerate(video_ids):
        # Progress: 15-50% for video processing
        percent = 15 + int((idx / total) * 35)

        with SessionLocal() as db:
            video = db.query(ProcessedVideo).filter(
                ProcessedVideo.video_id == video_id
            ).first()

            if not video or video.processing_status == "completed":
                continue

            # Get channel name
            channel = db.query(Channel).filter(
                Channel.channel_id == video.channel_id
            ).first()
            channel_name = channel.channel_name if channel else "Unknown"

            update_state_fn(
                state="PROGRESS",
                meta={
                    "phase": "processing",
                    "current": idx + 1,
                    "total": total,
                    "percent": percent,
                    "message": f"Verarbeite Video {idx + 1}/{total}...",
                    "current_channel": channel_name,
                    "current_video_title": video.title[:50] + "..." if len(video.title) > 50 else video.title,
                }
            )

            video.processing_status = "processing"
            db.commit()

            try:
                # Fetch transcript
                transcript_result = transcript_service.get_transcript(video_id)
                video.transcript = transcript_result.text
                video.transcript_source = transcript_result.source

                # Summarize
                summary = summarization_service.summarize_video(
                    transcript=transcript_result.text,
                    title=video.title,
                    channel=channel_name,
                    duration_seconds=video.duration_seconds,
                )

                video.category = summary.category.value
                video.summary = {
                    "core_message": summary.core_message,
                    "detailed_summary": summary.detailed_summary,
                    "key_takeaways": summary.key_takeaways,
                    "timestamps": [
                        {"time": ts.time, "description": ts.description}
                        for ts in summary.timestamps
                    ],
                    "action_items": summary.action_items,
                }
                video.processing_status = "completed"
                video.processed_at = datetime.now(timezone.utc)
                video.error_message = None
                db.commit()

                logger.info(f"Video {video_id} processed: {summary.category.value}")

            except Exception as e:
                logger.error(f"Error processing video {video_id}: {e}")
                video.processing_status = "failed"
                video.error_message = str(e)
                video.retry_count = (video.retry_count or 0) + 1
                video.last_retry_at = datetime.now(timezone.utc)
                db.commit()


@celery_app.task(bind=True, name="app.tasks.generate_and_send_digest")
def generate_and_send_digest(
    self,
    trigger_reason: str = "scheduled",
    check_for_new: bool = True,
) -> dict[str, Any]:
    """
    Generate digest from unprocessed videos and send via email.

    If check_for_new=True, first syncs channels and processes new videos.

    Args:
        trigger_reason: "scheduled", "threshold", or "manual"
        check_for_new: Whether to fetch new videos from YouTube first

    Returns:
        dict with status, video_count, email_status
    """
    logger.info(f"Generating digest (trigger: {trigger_reason}, check_for_new: {check_for_new})")

    # Helper to safely update task state (works outside Celery context)
    def safe_update_state(state, meta):
        if self.request.id:
            self.update_state(state=state, meta=meta)

    try:
        # Phase 1: Sync channels and fetch new videos if requested
        if check_for_new:
            safe_update_state(
                state="PROGRESS",
                meta={
                    "phase": "sync",
                    "current": 0,
                    "total": 0,
                    "percent": 5,
                    "message": "Lade neue Videos von YouTube...",
                }
            )

            # Run sync logic inline (not as separate task)
            new_video_ids = _sync_channels_and_fetch_videos(self, safe_update_state)

            # Phase 2: Process new videos
            if new_video_ids:
                safe_update_state(
                    state="PROGRESS",
                    meta={
                        "phase": "processing",
                        "current": 0,
                        "total": len(new_video_ids),
                        "percent": 15,
                        "message": f"{len(new_video_ids)} Videos werden verarbeitet...",
                    }
                )
                _process_videos_sync(self, new_video_ids, safe_update_state)

        # Update progress: initializing digest generation
        safe_update_state(
            state="PROGRESS",
            meta={
                "phase": "initializing",
                "current": 0,
                "total": 0,
                "percent": 50,
                "message": "Lade verarbeitete Videos...",
            }
        )

        with SessionLocal() as db:
            # Get videos not yet included in any digest
            videos = db.query(ProcessedVideo).filter(
                and_(
                    ProcessedVideo.processing_status == "completed",
                    ProcessedVideo.included_in_digest_id.is_(None)
                )
            ).order_by(ProcessedVideo.published_at.desc()).all()

            if not videos:
                logger.info("No videos to include in digest")
                return {
                    "status": "skipped",
                    "message": "No videos to include in digest",
                    "video_count": 0,
                }

            total_videos = len(videos)

            # Update progress: videos loaded
            safe_update_state(
                state="PROGRESS",
                meta={
                    "phase": "generating_digest",
                    "current": 0,
                    "total": total_videos,
                    "percent": 55,
                    "message": f"{total_videos} Videos gefunden",
                }
            )

            # Determine period
            period_end = datetime.now(timezone.utc)
            period_start = min(v.published_at for v in videos if v.published_at)
            if not period_start:
                period_start = period_end - timedelta(days=14)

            # Update progress: generating digest
            safe_update_state(
                state="PROGRESS",
                meta={
                    "phase": "generating_digest",
                    "current": 0,
                    "total": total_videos,
                    "percent": 70,
                    "message": "Generiere Digest...",
                }
            )

            # Generate digest
            generator = DigestGenerator()
            digest_result = generator.generate(
                videos=videos,
                period_start=period_start,
                period_end=period_end,
            )

            # Update progress: digest generated
            safe_update_state(
                state="PROGRESS",
                meta={
                    "phase": "generating_digest",
                    "current": total_videos,
                    "total": total_videos,
                    "percent": 80,
                    "message": f"Digest mit {digest_result.video_count} Videos erstellt",
                }
            )

            # Create digest history record
            digest_history = DigestHistory(
                period_start=period_start,
                period_end=period_end,
                video_count=digest_result.video_count,
                total_duration_seconds=digest_result.total_duration_seconds,
                category_counts=digest_result.category_counts,
                email_status="pending",
                trigger_reason=trigger_reason,
                recipient_email=settings.email_to_address,
            )
            db.add(digest_history)
            db.flush()

            # Update progress: sending email
            safe_update_state(
                state="PROGRESS",
                meta={
                    "phase": "sending_email",
                    "current": total_videos,
                    "total": total_videos,
                    "percent": 90,
                    "message": "Sende E-Mail...",
                }
            )

            # Send email
            email_service = EmailService()
            email_result = email_service.send_digest(
                html_content=digest_result.html,
                plain_content=digest_result.plain_text,
                subject=digest_result.subject,
            )

            if email_result.success:
                digest_history.email_status = "sent"
                digest_history.sent_at = datetime.now(timezone.utc)

                # Mark videos as included in this digest
                for video in videos[:50]:  # Max 50 per digest
                    video.included_in_digest_id = digest_history.id

                db.commit()

                logger.info(
                    f"Digest sent successfully: {digest_result.video_count} videos"
                )

                return {
                    "status": "sent",
                    "video_count": digest_result.video_count,
                    "digest_id": digest_history.id,
                    "email_status": "sent",
                }
            else:
                digest_history.email_status = "failed"
                digest_history.email_error = email_result.message
                db.commit()

                raise Exception(f"Email failed: {email_result.message}")

    except Exception as e:
        logger.error(f"Error generating digest: {e}")
        raise self.retry(exc=e, countdown=300, max_retries=2)


@celery_app.task(bind=True, name="app.tasks.sync_channel_metadata")
def sync_channel_metadata(self, fetch_videos: bool = True) -> dict[str, Any]:
    """
    Synchronize channel metadata and optionally fetch videos.

    Updates existing channels with current names, thumbnails, and descriptions.
    If fetch_videos=True, also retrieves videos (last 14 days OR 10 videos per channel).

    Args:
        fetch_videos: Whether to also fetch new videos from channels

    Returns:
        dict with status, channels_updated, videos_found, videos_queued
    """
    logger.info(f"Starting channel sync (fetch_videos={fetch_videos})")

    try:
        youtube = YouTubeService()
        subscriptions = youtube.get_subscriptions()

        channels_updated = 0
        new_videos_found = 0
        videos_queued = 0

        with SessionLocal() as db:
            # Phase 1: Get detailed channel info (including descriptions)
            channel_ids = [sub["channel_id"] for sub in subscriptions if sub.get("channel_id")]
            channel_details = youtube.get_channel_details(channel_ids)
            details_map = {c["channel_id"]: c for c in channel_details}

            # Phase 2: Update channel metadata
            for sub in subscriptions:
                channel_id = sub["channel_id"]
                channel_name = sub.get("channel_name", "Unknown")
                thumbnail_url = sub.get("thumbnail_url")

                # Get detailed info including description
                details = details_map.get(channel_id, {})
                description = details.get("description", "")

                channel = db.query(Channel).filter(
                    Channel.channel_id == channel_id
                ).first()

                if not channel:
                    # Create new channel
                    channel = Channel(
                        channel_id=channel_id,
                        channel_name=channel_name,
                        channel_url=f"https://www.youtube.com/channel/{channel_id}",
                        thumbnail_url=thumbnail_url,
                        description=description,
                        is_active=True,
                    )
                    db.add(channel)
                    channels_updated += 1
                    logger.info(f"Created channel: {channel_name}")
                else:
                    # Check if anything changed
                    changed = False
                    if channel.channel_name != channel_name:
                        channel.channel_name = channel_name
                        changed = True
                    if channel.thumbnail_url != thumbnail_url:
                        channel.thumbnail_url = thumbnail_url
                        changed = True
                    if channel.description != description:
                        channel.description = description
                        changed = True

                    if changed:
                        channels_updated += 1
                        logger.info(f"Updated channel: {channel_name}")

                # Always update last_checked when syncing
                channel.last_checked = datetime.now(timezone.utc)

            db.commit()

            # Phase 2: Fetch videos if requested
            video_ids_to_process = []

            if fetch_videos:
                # Use 14 days as date filter, but fetch up to 10 videos per channel
                since_date = datetime.now(timezone.utc) - timedelta(days=14)

                for sub in subscriptions:
                    channel_id = sub["channel_id"]
                    channel_name = sub.get("channel_name", "Unknown")

                    try:
                        # Fetch up to 10 videos per channel (will stop at since_date if reached)
                        videos = youtube.get_channel_videos(
                            channel_id,
                            since_date=since_date,
                            max_results=10,
                        )

                        # Get video details (duration, live status)
                        if videos:
                            video_ids = [v["video_id"] for v in videos if v.get("video_id")]
                            video_details = youtube.get_video_details(video_ids)
                            details_map = {v["video_id"]: v for v in video_details}

                            for video in videos:
                                video_id = video.get("video_id")
                                if not video_id:
                                    continue

                                # Check if already processed
                                existing = db.query(ProcessedVideo).filter(
                                    ProcessedVideo.video_id == video_id
                                ).first()

                                if existing:
                                    continue

                                # Get details
                                details = details_map.get(video_id, {})

                                # Skip short videos (< 2 min) and livestreams
                                duration = details.get("duration_seconds", 0)
                                if duration < 120:
                                    logger.debug(f"Skipping short video: {video_id} ({duration}s < 2min)")
                                    continue

                                if details.get("liveStreamingDetails"):
                                    logger.debug(f"Skipping livestream: {video_id}")
                                    continue

                                # Parse published_at
                                published_at = video.get("published_at")
                                if isinstance(published_at, str):
                                    try:
                                        published_at = datetime.fromisoformat(
                                            published_at.replace("Z", "+00:00")
                                        )
                                    except ValueError:
                                        published_at = datetime.now(timezone.utc)

                                # Create pending video record
                                new_video = ProcessedVideo(
                                    video_id=video_id,
                                    channel_id=channel_id,
                                    title=video.get("title", ""),
                                    description=video.get("description", ""),
                                    duration_seconds=duration,
                                    published_at=published_at,
                                    thumbnail_url=details.get("thumbnail_url") or video.get("thumbnail_url"),
                                    processing_status="pending",
                                )
                                db.add(new_video)

                                new_videos_found += 1
                                video_ids_to_process.append(video_id)

                        logger.info(f"Channel {channel_name}: found {len(videos)} videos")

                    except Exception as e:
                        logger.warning(f"Error fetching videos for {channel_name}: {e}")
                        continue

            # Commit all changes BEFORE queueing tasks
            db.commit()

        # Queue video processing AFTER commit (outside the db context)
        for video_id in video_ids_to_process:
            process_video.delay(video_id)
            videos_queued += 1

        result = {
            "status": "completed",
            "channels_updated": channels_updated,
            "total_subscriptions": len(subscriptions),
            "new_videos_found": new_videos_found,
            "videos_queued": videos_queued,
        }
        logger.info(f"Channel sync complete: {result}")
        return result

    except Exception as e:
        logger.error(f"Error in sync_channel_metadata: {e}")
        raise self.retry(exc=e, countdown=60, max_retries=2)
