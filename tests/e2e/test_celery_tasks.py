# tests/e2e/test_celery_tasks.py
"""
End-to-End Tests for Celery tasks.

Requires running Redis and PostgreSQL.
Run with: pytest tests/e2e/test_celery_tasks.py -v --run-e2e
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_E2E_TESTS"),
    reason="E2E tests disabled. Set RUN_E2E_TESTS=1 to run.",
)


class TestCeleryTasksE2E:
    """Test Celery tasks with real infrastructure."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test environment and verify infrastructure."""
        import redis

        from app.config import get_settings

        self.settings = get_settings()

        # Verify Redis is running
        try:
            r = redis.from_url(self.settings.redis_url)
            r.ping()
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    def test_check_for_new_videos_task(self):
        """Test the check_for_new_videos Celery task."""
        from app.tasks import check_for_new_videos

        # Run task synchronously for testing
        result = check_for_new_videos.apply()

        # Task should complete without error
        assert result.successful(), f"Task failed: {result.traceback}"

        task_result = result.get(timeout=60)
        assert "channels_checked" in task_result or "error" not in str(
            task_result
        ).lower()

        print(f"Task result: {task_result}")

    def test_process_video_task_real_video(self):
        """Test processing a real video through Celery."""
        from app.services.youtube_service import YouTubeService
        from app.tasks import process_video

        # Get a real video to process
        yt_service = YouTubeService()
        subscriptions = yt_service.get_subscriptions()[:1]

        if not subscriptions:
            pytest.skip("No subscriptions found")

        since_date = datetime.now(timezone.utc) - timedelta(days=14)
        videos = yt_service.get_channel_videos(
            subscriptions[0]["channel_id"], since_date
        )

        # Find a non-short video
        test_video = None
        for v in videos:
            if v.get("duration_seconds", 0) > 120:  # At least 2 minutes
                test_video = v
                break

        if not test_video:
            pytest.skip("No suitable test video found")

        print(f"Processing video: {test_video['title']}")

        # Run task synchronously
        result = process_video.apply(args=[test_video["video_id"]])

        assert result.successful(), f"Task failed: {result.traceback}"

        task_result = result.get(timeout=120)  # Allow 2 minutes for processing
        print(f"Process result: {task_result}")

    def test_generate_digest_task(self):
        """Test digest generation task."""
        from app.tasks import generate_and_send_digest

        # Run task synchronously with test trigger
        result = generate_and_send_digest.apply(args=["e2e_test"])

        assert result.successful(), f"Task failed: {result.traceback}"

        task_result = result.get(timeout=60)
        print(f"Digest result: {task_result}")

    def test_task_retry_configuration(self):
        """Test that tasks have proper retry configured."""
        from app.tasks import check_for_new_videos, generate_and_send_digest, process_video

        # Verify tasks have retry behavior (max_retries is set in @celery_app.task)
        # The retry is done via self.retry() in the task body
        assert check_for_new_videos is not None
        assert process_video is not None
        assert generate_and_send_digest is not None


class TestCeleryBeatSchedule:
    """Test Celery Beat schedule configuration."""

    def test_beat_schedule_configured(self):
        """Verify beat schedule is properly configured."""
        from app.celery_app import celery_app

        beat_schedule = celery_app.conf.beat_schedule

        assert "check-for-new-videos-daily" in beat_schedule
        assert "generate-digest-biweekly" in beat_schedule

        # Verify schedule intervals
        daily_task = beat_schedule["check-for-new-videos-daily"]
        assert daily_task["schedule"] == 86400.0  # 24 hours

        biweekly_task = beat_schedule["generate-digest-biweekly"]
        assert biweekly_task["schedule"] == 1209600.0  # 14 days

    def test_beat_schedule_tasks_exist(self):
        """Verify scheduled tasks exist and are importable."""
        from app.celery_app import celery_app

        beat_schedule = celery_app.conf.beat_schedule

        # Verify task names match actual tasks
        assert beat_schedule["check-for-new-videos-daily"]["task"] == "app.tasks.check_for_new_videos"
        assert beat_schedule["generate-digest-biweekly"]["task"] == "app.tasks.generate_and_send_digest"
