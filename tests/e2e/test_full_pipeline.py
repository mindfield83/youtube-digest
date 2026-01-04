# tests/e2e/test_full_pipeline.py
"""
End-to-End Tests for the complete YouTube Digest pipeline.

These tests use REAL APIs:
- YouTube Data API (requires valid OAuth token)
- Gemini API (requires GEMINI_API_KEY)
- SMTP (sends real test emails)

Run with: pytest tests/e2e/ -v --run-e2e
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

# Skip all E2E tests if not explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_E2E_TESTS"),
    reason="E2E tests disabled. Set RUN_E2E_TESTS=1 to run.",
)


class TestFullPipeline:
    """Test the complete video processing pipeline with real APIs."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test environment."""
        from app.config import get_settings

        self.settings = get_settings()

        # Verify required credentials exist
        assert self.settings.gemini_api_key, "GEMINI_API_KEY required for E2E tests"
        assert (
            self.settings.youtube_token_path.exists()
        ), "YouTube OAuth token required"

    def test_youtube_api_fetch_subscriptions(self):
        """Test fetching real YouTube subscriptions."""
        from app.services.youtube_service import YouTubeService

        service = YouTubeService()
        subscriptions = service.get_subscriptions()

        assert subscriptions is not None
        assert len(subscriptions) > 0

        # Verify subscription structure
        first_sub = subscriptions[0]
        assert "channel_id" in first_sub
        assert "channel_title" in first_sub
        print(f"Found {len(subscriptions)} subscriptions")

    def test_youtube_api_fetch_channel_videos(self):
        """Test fetching videos from a known channel."""
        from app.services.youtube_service import YouTubeService

        service = YouTubeService()

        # Get first subscription
        subscriptions = service.get_subscriptions()
        assert len(subscriptions) > 0

        channel_id = subscriptions[0]["channel_id"]

        # Fetch videos from last 30 days
        since_date = datetime.now(timezone.utc) - timedelta(days=30)
        videos = service.get_channel_videos(channel_id, since_date)

        print(
            f"Found {len(videos)} videos from channel {subscriptions[0]['channel_title']}"
        )

        if videos:
            video = videos[0]
            assert "video_id" in video
            assert "title" in video
            assert "duration_seconds" in video

    def test_transcript_extraction_real_video(self):
        """Test transcript extraction from a real video."""
        from app.services.transcript_service import TranscriptService

        # Use a known video with captions (Rick Astley - Never Gonna Give You Up)
        # This video is stable and always has captions
        test_video_id = "dQw4w9WgXcQ"

        service = TranscriptService()
        result = service.get_transcript(test_video_id)

        assert result is not None
        assert "transcript" in result
        assert len(result["transcript"]) > 100
        assert result["language"] in ["en", "de"]
        print(
            f"Transcript length: {len(result['transcript'])} chars, "
            f"language: {result['language']}"
        )

    def test_gemini_summarization_real_api(self):
        """Test Gemini summarization with real API call."""
        from app.services.summarization_service import SummarizationService

        service = SummarizationService()

        # Sample transcript for testing
        test_transcript = """
        In this video, we'll explore the latest developments in artificial intelligence,
        specifically focusing on large language models and their applications in software
        development. We'll cover three main topics: code generation, debugging assistance,
        and documentation automation. First, let's look at how AI can help write better code...
        """

        test_metadata = {
            "title": "AI in Software Development - A Complete Guide",
            "channel_title": "Tech Tutorials",
            "duration_seconds": 900,
        }

        result = service.summarize_video(test_transcript, test_metadata)

        assert result is not None
        assert result.category in [
            "Claude Code",
            "Coding/AI Allgemein",
            "Brettspiele",
            "Gesundheit",
            "Sport",
            "Beziehung/Sexualität",
            "Beachvolleyball",
            "Sonstige",
        ]
        assert len(result.core_message) > 20
        assert len(result.detailed_summary) > 100
        assert len(result.key_takeaways) > 0

        print(f"Category: {result.category}")
        print(f"Core message: {result.core_message[:100]}...")

    def test_email_sending_real_smtp(self):
        """Test sending a real test email via SMTP."""
        from app.services.email_service import EmailService

        service = EmailService()

        # First test connection
        connection_ok = service.test_connection()
        assert connection_ok, "SMTP connection failed"

        # Send test email
        result = service.send_test_email()
        assert result is True, "Failed to send test email"

        print("Test email sent successfully")

    def test_full_pipeline_single_video(self):
        """
        Test complete pipeline: YouTube -> Transcript -> Summary -> Digest.
        Does NOT send email (use test_email_sending_real_smtp for that).
        """
        from app.models import ProcessedVideo
        from app.services.digest_generator import DigestGenerator
        from app.services.summarization_service import SummarizationService
        from app.services.transcript_service import TranscriptService
        from app.services.youtube_service import YouTubeService

        # Step 1: Get a real video from subscriptions
        yt_service = YouTubeService()
        subscriptions = yt_service.get_subscriptions()[:3]  # First 3 channels

        test_video = None
        for sub in subscriptions:
            since_date = datetime.now(timezone.utc) - timedelta(days=30)
            videos = yt_service.get_channel_videos(sub["channel_id"], since_date)

            # Find a video that's not a short (> 60 seconds)
            for v in videos:
                if v.get("duration_seconds", 0) > 60:
                    test_video = v
                    break
            if test_video:
                break

        if not test_video:
            pytest.skip("No suitable test video found in subscriptions")

        print(f"Testing with video: {test_video['title']}")

        # Step 2: Get transcript
        transcript_service = TranscriptService()
        transcript_result = transcript_service.get_transcript(test_video["video_id"])

        if not transcript_result:
            pytest.skip(f"Could not get transcript for video {test_video['video_id']}")

        print(f"Got transcript: {len(transcript_result['transcript'])} chars")

        # Step 3: Summarize
        summary_service = SummarizationService()
        summary = summary_service.summarize_video(
            transcript_result["transcript"],
            {
                "title": test_video["title"],
                "channel_title": test_video.get("channel_title", "Unknown"),
                "duration_seconds": test_video.get("duration_seconds", 0),
            },
        )

        assert summary is not None
        print(f"Summary category: {summary.category}")

        # Step 4: Generate digest HTML (without sending)
        # Create a mock ProcessedVideo for the generator
        mock_video = ProcessedVideo(
            video_id=test_video["video_id"],
            channel_id=test_video.get("channel_id", "unknown"),
            title=test_video["title"],
            channel_title=test_video.get("channel_title", "Unknown"),
            duration_seconds=test_video.get("duration_seconds", 0),
            published_at=datetime.now(timezone.utc),
            category=summary.category,
            summary=summary.model_dump(),
            transcript_source="youtube",
            processed_at=datetime.now(timezone.utc),
            processing_status="completed",
        )

        digest_generator = DigestGenerator()
        html_content = digest_generator.generate_html([mock_video])

        assert len(html_content) > 500
        assert test_video["title"] in html_content

        print("Full pipeline test completed successfully!")
        print(f"Generated digest HTML: {len(html_content)} chars")


class TestPipelineErrorHandling:
    """Test error handling in the pipeline."""

    def test_transcript_fallback_to_supadata(self):
        """Test that Supadata fallback works for videos without captions."""
        from app.config import get_settings
        from app.services.transcript_service import TranscriptService

        settings = get_settings()
        if not settings.supadata_api_key:
            pytest.skip("SUPADATA_API_KEY required for this test")

        # This would need a video ID known to have no YouTube captions
        # For now, we just verify the service initializes correctly
        service = TranscriptService()
        assert service is not None

    def test_invalid_video_id_handling(self):
        """Test graceful handling of invalid video IDs."""
        from app.services.transcript_service import TranscriptService

        service = TranscriptService()
        result = service.get_transcript("invalid_video_id_12345")

        # Should return None, not raise exception
        assert result is None
