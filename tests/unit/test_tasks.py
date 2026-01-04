# tests/unit/test_tasks.py
"""Unit tests for Celery tasks."""
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


def create_mock_session_context(mock_db):
    """Create a context manager mock for SessionLocal."""
    @contextmanager
    def session_context():
        yield mock_db
    return session_context


class TestCheckForNewVideos:
    """Tests for check_for_new_videos task."""

    @patch("app.tasks.settings")
    @patch("app.tasks.YouTubeService")
    @patch("app.tasks.SessionLocal")
    def test_fetches_subscriptions(self, mock_session, mock_yt_service, mock_settings):
        """Task should fetch YouTube subscriptions."""
        from app.tasks import check_for_new_videos

        mock_yt = MagicMock()
        mock_yt.get_subscriptions.return_value = []
        mock_yt_service.return_value = mock_yt

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_session.side_effect = [create_mock_session_context(mock_db)(), create_mock_session_context(mock_db)()]

        mock_settings.digest_video_threshold = 10

        check_for_new_videos()

        mock_yt.get_subscriptions.assert_called_once()

    @patch("app.tasks.settings")
    @patch("app.tasks.YouTubeService")
    @patch("app.tasks.SessionLocal")
    def test_queues_new_videos_for_processing(self, mock_session, mock_yt_service, mock_settings):
        """Task should queue process_video for each new video."""
        from app.tasks import check_for_new_videos, process_video

        mock_yt = MagicMock()
        mock_yt.get_subscriptions.return_value = [
            {"channel_id": "UC123", "title": "Test Channel"}
        ]
        mock_yt.get_channel_videos.return_value = [
            {"video_id": "vid1", "title": "Test Video"}
        ]
        mock_yt_service.return_value = mock_yt

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_session.side_effect = [create_mock_session_context(mock_db)(), create_mock_session_context(mock_db)()]

        mock_settings.digest_video_threshold = 10

        with patch.object(process_video, "delay") as mock_delay:
            check_for_new_videos()
            mock_delay.assert_called_once_with("vid1")


class TestProcessVideo:
    """Tests for process_video task."""

    @patch("app.tasks.TranscriptService")
    @patch("app.tasks.SummarizationService")
    @patch("app.tasks.SessionLocal")
    def test_fetches_transcript_and_summarizes(
        self, mock_session, mock_sum_service, mock_trans_service
    ):
        """Task should fetch transcript and create summary."""
        from app.tasks import process_video

        mock_trans = MagicMock()
        mock_trans.fetch_transcript.return_value = MagicMock(
            text="Test transcript",
            language="de",
            source="youtube"
        )
        mock_trans_service.return_value = mock_trans

        mock_sum = MagicMock()
        mock_sum.summarize_video.return_value = MagicMock(
            category=MagicMock(value="Sonstige"),
            core_message="Test message",
            detailed_summary="Test summary",
            key_takeaways=["Point 1"],
            timestamps=[],
            action_items=[]
        )
        mock_sum_service.return_value = mock_sum

        mock_video = MagicMock()
        mock_video.video_id = "vid1"
        mock_video.title = "Test Video"
        mock_video.channel = MagicMock(channel_name="Test Channel")
        mock_video.duration_seconds = 600
        mock_video.processing_status = "pending"

        mock_channel = MagicMock()
        mock_channel.channel_name = "Test Channel"

        mock_db = MagicMock()
        # First query for video, second for channel
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_video, mock_channel
        ]
        mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)

        result = process_video("vid1")

        assert result["status"] == "completed"
        mock_trans.fetch_transcript.assert_called_once_with("vid1")
        mock_sum.summarize_video.assert_called_once()


class TestGenerateAndSendDigest:
    """Tests for generate_and_send_digest task."""

    @patch("app.tasks.DigestGenerator")
    @patch("app.tasks.EmailService")
    @patch("app.tasks.SessionLocal")
    def test_generates_and_sends_email(
        self, mock_session, mock_email_service, mock_digest_gen
    ):
        """Task should generate digest and send email."""
        from app.tasks import generate_and_send_digest

        mock_video = MagicMock()
        mock_video.video_id = "vid1"
        mock_video.published_at = datetime.now(timezone.utc)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_video]
        mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_gen = MagicMock()
        mock_gen.generate_digest.return_value = MagicMock(
            html="<html>Test</html>",
            plain_text="Test",
            subject="YouTube Digest",
            video_count=1,
            total_duration_seconds=600,
            category_counts={"Sonstige": 1}
        )
        mock_digest_gen.return_value = mock_gen

        mock_email = MagicMock()
        mock_email.send_digest.return_value = MagicMock(success=True)
        mock_email_service.return_value = mock_email

        result = generate_and_send_digest()

        assert result["status"] == "sent"
        mock_email.send_digest.assert_called_once()

    @patch("app.tasks.SessionLocal")
    def test_skips_when_no_videos(self, mock_session):
        """Task should skip if no unprocessed videos."""
        from app.tasks import generate_and_send_digest

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_session.return_value.__exit__ = MagicMock(return_value=False)

        result = generate_and_send_digest()

        assert result["status"] == "skipped"
        assert "no videos" in result["message"].lower()
