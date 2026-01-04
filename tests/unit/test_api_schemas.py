# tests/unit/test_api_schemas.py
"""Unit tests for API schemas."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


class TestChannelSchema:
    """Tests for Channel response schema."""

    def test_channel_schema_valid(self):
        """Valid channel data should serialize."""
        from app.api.schemas import ChannelResponse

        channel = ChannelResponse(
            channel_id="UC123",
            channel_name="Test Channel",
            channel_url="https://youtube.com/channel/UC123",
            is_active=True,
            video_count=10,
        )
        assert channel.channel_id == "UC123"

    def test_channel_schema_optional_fields(self):
        """Optional fields should have defaults."""
        from app.api.schemas import ChannelResponse

        channel = ChannelResponse(
            channel_id="UC123",
            channel_name="Test Channel",
        )
        assert channel.channel_url is None
        assert channel.is_active is True


class TestVideoSchema:
    """Tests for Video response schema."""

    def test_video_schema_valid(self):
        """Valid video data should serialize."""
        from app.api.schemas import VideoResponse

        video = VideoResponse(
            video_id="vid123",
            title="Test Video",
            channel_name="Test Channel",
            category="Sonstige",
            processing_status="completed",
            duration_seconds=600,
        )
        assert video.video_id == "vid123"


class TestDigestSchema:
    """Tests for Digest response schema."""

    def test_digest_schema_valid(self):
        """Valid digest data should serialize."""
        from app.api.schemas import DigestResponse

        digest = DigestResponse(
            id=1,
            sent_at=datetime.now(timezone.utc),
            video_count=5,
            email_status="sent",
            trigger_reason="scheduled",
        )
        assert digest.id == 1


class TestStatusSchema:
    """Tests for system status schema."""

    def test_status_schema_valid(self):
        """Valid status data should serialize."""
        from app.api.schemas import SystemStatus

        status = SystemStatus(
            oauth_valid=True,
            worker_active=True,
            pending_videos=5,
            last_digest_at=datetime.now(timezone.utc),
            next_scheduled_digest=datetime.now(timezone.utc),
        )
        assert status.oauth_valid is True
