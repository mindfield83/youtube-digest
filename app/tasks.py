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
                transcript_result = transcript_service.fetch_transcript(video_id)

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


@celery_app.task(bind=True, name="app.tasks.generate_and_send_digest")
def generate_and_send_digest(
    self,
    trigger_reason: str = "scheduled"
) -> dict[str, Any]:
    """
    Generate digest from unprocessed videos and send via email.

    Args:
        trigger_reason: "scheduled", "threshold", or "manual"

    Returns:
        dict with status, video_count, email_status
    """
    logger.info(f"Generating digest (trigger: {trigger_reason})")

    # Update progress: initializing
    self.update_state(
        state="PROGRESS",
        meta={
            "phase": "initializing",
            "current": 0,
            "total": 0,
            "percent": 5,
            "message": "Lade Videos...",
        }
    )

    try:
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
            self.update_state(
                state="PROGRESS",
                meta={
                    "phase": "processing",
                    "current": 0,
                    "total": total_videos,
                    "percent": 10,
                    "message": f"{total_videos} Videos gefunden",
                }
            )

            # Determine period
            period_end = datetime.now(timezone.utc)
            period_start = min(v.published_at for v in videos if v.published_at)
            if not period_start:
                period_start = period_end - timedelta(days=14)

            # Update progress: generating digest
            self.update_state(
                state="PROGRESS",
                meta={
                    "phase": "generating_digest",
                    "current": 0,
                    "total": total_videos,
                    "percent": 50,
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
            self.update_state(
                state="PROGRESS",
                meta={
                    "phase": "generating_digest",
                    "current": total_videos,
                    "total": total_videos,
                    "percent": 70,
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
            self.update_state(
                state="PROGRESS",
                meta={
                    "phase": "sending_email",
                    "current": total_videos,
                    "total": total_videos,
                    "percent": 85,
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
def sync_channel_metadata(self) -> dict[str, Any]:
    """
    Synchronize channel metadata (names, thumbnails) from YouTube API.

    Updates existing channels with current names and thumbnails from subscriptions.

    Returns:
        dict with status, channels_updated count
    """
    logger.info("Starting channel metadata sync")

    try:
        youtube = YouTubeService()
        subscriptions = youtube.get_subscriptions()

        channels_updated = 0

        with SessionLocal() as db:
            for sub in subscriptions:
                channel_id = sub["channel_id"]
                channel_name = sub.get("channel_name", "Unknown")
                thumbnail_url = sub.get("thumbnail_url")

                channel = db.query(Channel).filter(
                    Channel.channel_id == channel_id
                ).first()

                if channel:
                    # Update metadata if changed
                    if channel.channel_name != channel_name or channel.thumbnail_url != thumbnail_url:
                        channel.channel_name = channel_name
                        channel.thumbnail_url = thumbnail_url
                        channels_updated += 1
                        logger.info(f"Updated channel: {channel_name}")

            db.commit()

        result = {
            "status": "completed",
            "channels_updated": channels_updated,
            "total_subscriptions": len(subscriptions),
        }
        logger.info(f"Channel sync complete: {result}")
        return result

    except Exception as e:
        logger.error(f"Error in sync_channel_metadata: {e}")
        raise self.retry(exc=e, countdown=60, max_retries=2)
