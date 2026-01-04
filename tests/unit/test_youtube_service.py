"""
Unit tests for YouTube Service
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from app.services.youtube_service import (
    YouTubeService,
    YouTubeServiceError,
    OAuthError,
    QuotaExceededError,
    parse_iso8601_duration,
    is_valid_video,
)


class TestParseIso8601Duration:
    """Tests for ISO 8601 duration parsing."""

    def test_minutes_and_seconds(self):
        """PT15M30S should be 930 seconds."""
        assert parse_iso8601_duration("PT15M30S") == 930

    def test_hours_and_minutes(self):
        """PT1H30M should be 5400 seconds."""
        assert parse_iso8601_duration("PT1H30M") == 5400

    def test_seconds_only(self):
        """PT45S should be 45 seconds."""
        assert parse_iso8601_duration("PT45S") == 45

    def test_hours_minutes_seconds(self):
        """PT2H15M30S should be 8130 seconds."""
        assert parse_iso8601_duration("PT2H15M30S") == 8130

    def test_zero_duration(self):
        """PT0S should be 0 seconds."""
        assert parse_iso8601_duration("PT0S") == 0

    def test_invalid_duration(self):
        """Invalid duration should return 0."""
        assert parse_iso8601_duration("invalid") == 0
        assert parse_iso8601_duration("") == 0


class TestIsValidVideo:
    """Tests for video filtering logic."""

    def test_regular_video_passes(self):
        """Normal video (>60s, not live) should pass."""
        video = {
            "snippet": {"title": "Test Video", "liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT15M30S"},
        }
        assert is_valid_video(video) is True

    def test_short_filtered(self):
        """Videos under 60 seconds (Shorts) should be filtered."""
        video = {
            "snippet": {"title": "Short Video", "liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT45S"},
        }
        assert is_valid_video(video) is False

    def test_exactly_60_seconds_passes(self):
        """Videos exactly 60 seconds should pass."""
        video = {
            "snippet": {"title": "Borderline Video", "liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT1M"},
        }
        assert is_valid_video(video) is True

    def test_livestream_with_details_filtered(self):
        """Videos with liveStreamingDetails should be filtered."""
        video = {
            "snippet": {"title": "Livestream", "liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT1H"},
            "liveStreamingDetails": {"actualStartTime": "2025-01-01T10:00:00Z"},
        }
        assert is_valid_video(video) is False

    def test_live_broadcast_filtered(self):
        """Videos with liveBroadcastContent='live' should be filtered."""
        video = {
            "snippet": {"title": "Live Now", "liveBroadcastContent": "live"},
            "contentDetails": {"duration": "PT0S"},
        }
        assert is_valid_video(video) is False

    def test_upcoming_filtered(self):
        """Videos with liveBroadcastContent='upcoming' should be filtered."""
        video = {
            "snippet": {"title": "Premiere", "liveBroadcastContent": "upcoming"},
            "contentDetails": {"duration": "PT0S"},
        }
        assert is_valid_video(video) is False

    def test_missing_duration_short(self):
        """Videos with missing duration default to 0 (filtered as Short)."""
        video = {
            "snippet": {"title": "No Duration"},
            "contentDetails": {},
        }
        assert is_valid_video(video) is False


class TestYouTubeService:
    """Tests for YouTubeService class."""

    @pytest.fixture
    def mock_credentials(self):
        """Create mock OAuth credentials."""
        creds = Mock()
        creds.valid = True
        creds.expired = False
        return creds

    @pytest.fixture
    def mock_youtube_client(self):
        """Create mock YouTube API client."""
        return MagicMock()

    @pytest.fixture
    def service(self, mock_credentials, mock_youtube_client):
        """Create YouTubeService with mocked dependencies."""
        with patch.object(YouTubeService, '_load_credentials', return_value=mock_credentials):
            with patch('app.services.youtube_service.build', return_value=mock_youtube_client):
                svc = YouTubeService()
                svc._credentials = mock_credentials
                svc._youtube = mock_youtube_client
                return svc

    def test_get_channel_upload_playlist_id_uc_prefix(self, service):
        """UC prefix should be converted to UU."""
        assert service.get_channel_upload_playlist_id("UCxxxxxx") == "UUxxxxxx"

    def test_get_channel_upload_playlist_id_other(self, service):
        """Non-UC channels should query API."""
        service.youtube.channels().list.return_value.execute.return_value = {
            "items": [{
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "UUother"}
                }
            }]
        }

        result = service.get_channel_upload_playlist_id("other_channel")
        assert result == "UUother"

    def test_get_subscriptions_single_page(self, service):
        """Should parse subscriptions from single page response."""
        service.youtube.subscriptions().list.return_value.execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "title": "Channel 1",
                        "resourceId": {"channelId": "UC111"},
                        "thumbnails": {"default": {"url": "http://thumb1.jpg"}},
                    }
                },
                {
                    "snippet": {
                        "title": "Channel 2",
                        "resourceId": {"channelId": "UC222"},
                        "thumbnails": {"default": {"url": "http://thumb2.jpg"}},
                    }
                },
            ],
            "nextPageToken": None,
        }

        channels = service.get_subscriptions()

        assert len(channels) == 2
        assert channels[0]["channel_id"] == "UC111"
        assert channels[0]["channel_name"] == "Channel 1"
        assert channels[1]["channel_id"] == "UC222"

    def test_get_subscriptions_pagination(self, service):
        """Should handle pagination for many subscriptions."""
        # First page
        first_response = {
            "items": [{"snippet": {"title": f"Ch{i}", "resourceId": {"channelId": f"UC{i}"}}} for i in range(50)],
            "nextPageToken": "page2",
        }
        # Second page
        second_response = {
            "items": [{"snippet": {"title": f"Ch{i}", "resourceId": {"channelId": f"UC{i}"}}} for i in range(50, 75)],
        }

        service.youtube.subscriptions().list.return_value.execute.side_effect = [
            first_response, second_response
        ]

        channels = service.get_subscriptions()

        assert len(channels) == 75

    def test_get_video_details_filters_shorts(self, service):
        """Video details should include duration for filtering."""
        service.youtube.videos().list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "vid1",
                    "snippet": {
                        "title": "Normal Video",
                        "description": "desc",
                        "publishedAt": "2025-01-01T10:00:00Z",
                        "channelId": "UC111",
                        "channelTitle": "Channel",
                        "liveBroadcastContent": "none",
                    },
                    "contentDetails": {"duration": "PT10M"},
                },
                {
                    "id": "vid2",
                    "snippet": {
                        "title": "Short",
                        "description": "desc",
                        "publishedAt": "2025-01-01T11:00:00Z",
                        "channelId": "UC111",
                        "channelTitle": "Channel",
                        "liveBroadcastContent": "none",
                    },
                    "contentDetails": {"duration": "PT30S"},
                },
            ]
        }

        videos = service.get_video_details(["vid1", "vid2"])

        assert len(videos) == 2
        assert videos[0]["duration_seconds"] == 600  # 10 minutes
        assert videos[1]["duration_seconds"] == 30   # 30 seconds

    def test_get_video_details_batches_large_lists(self, service):
        """Should batch requests for >50 video IDs."""
        video_ids = [f"vid{i}" for i in range(75)]

        service.youtube.videos().list.return_value.execute.side_effect = [
            {"items": [{"id": f"vid{i}", "snippet": {"title": f"Vid {i}"}, "contentDetails": {"duration": "PT5M"}} for i in range(50)]},
            {"items": [{"id": f"vid{i}", "snippet": {"title": f"Vid {i}"}, "contentDetails": {"duration": "PT5M"}} for i in range(50, 75)]},
        ]

        videos = service.get_video_details(video_ids)

        assert len(videos) == 75
        assert service.youtube.videos().list.call_count == 2


class TestYouTubeServiceOAuth:
    """Tests for OAuth handling."""

    def test_oauth_error_when_no_credentials(self):
        """Should raise OAuthError when credentials missing."""
        with patch('pathlib.Path.exists', return_value=False):
            service = YouTubeService()
            with pytest.raises(OAuthError):
                _ = service.credentials

    def test_credentials_loaded_from_file(self):
        """Should load valid credentials from token file."""
        mock_creds = Mock()
        mock_creds.valid = True
        mock_creds.expired = False

        with patch('pathlib.Path.exists', return_value=True):
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_file', return_value=mock_creds):
                service = YouTubeService()
                creds = service.credentials

                assert creds == mock_creds

    def test_credentials_refreshed_when_expired(self):
        """Should refresh expired credentials when refresh succeeds."""
        mock_creds = Mock()
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token"

        # Setup the refresh behavior
        def do_refresh(request):
            # After refresh, credentials become valid
            mock_creds.valid = True
            mock_creds.expired = False

        mock_creds.refresh = Mock(side_effect=do_refresh)
        mock_creds.to_json = Mock(return_value='{}')
        # Initially not valid because expired
        mock_creds.valid = False

        with patch('pathlib.Path.exists', return_value=True):
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_file', return_value=mock_creds):
                with patch('builtins.open', MagicMock()):
                    service = YouTubeService()
                    creds = service.credentials

                    # Verify refresh was called
                    mock_creds.refresh.assert_called_once()
