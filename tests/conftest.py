"""
Pytest configuration and shared fixtures for YouTube Digest tests.
"""
import os
import pytest
from unittest.mock import Mock, MagicMock


# Ensure we're using test environment
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "true")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/youtube_digest_test")


@pytest.fixture
def mock_youtube_credentials():
    """Mock OAuth credentials for YouTube API."""
    creds = Mock()
    creds.valid = True
    creds.expired = False
    creds.refresh_token = "mock_refresh_token"
    return creds


@pytest.fixture
def mock_youtube_client():
    """Mock YouTube API client."""
    client = MagicMock()
    return client


@pytest.fixture
def sample_video_response():
    """Sample video response from YouTube API."""
    return {
        "items": [
            {
                "id": "test_video_1",
                "snippet": {
                    "title": "Test Video 1",
                    "description": "Description for test video 1",
                    "publishedAt": "2025-01-01T12:00:00Z",
                    "channelId": "UC_test_channel",
                    "channelTitle": "Test Channel",
                    "thumbnails": {
                        "default": {"url": "https://example.com/thumb_default.jpg"},
                        "medium": {"url": "https://example.com/thumb_medium.jpg"},
                    },
                    "liveBroadcastContent": "none",
                },
                "contentDetails": {
                    "duration": "PT15M30S",
                },
            },
            {
                "id": "test_video_2",
                "snippet": {
                    "title": "Test Video 2 - Short",
                    "description": "This is a YouTube Short",
                    "publishedAt": "2025-01-02T12:00:00Z",
                    "channelId": "UC_test_channel",
                    "channelTitle": "Test Channel",
                    "thumbnails": {
                        "default": {"url": "https://example.com/thumb2.jpg"},
                    },
                    "liveBroadcastContent": "none",
                },
                "contentDetails": {
                    "duration": "PT45S",  # Short: < 60 seconds
                },
            },
            {
                "id": "test_video_3",
                "snippet": {
                    "title": "Test Livestream",
                    "description": "Live content",
                    "publishedAt": "2025-01-03T12:00:00Z",
                    "channelId": "UC_test_channel",
                    "channelTitle": "Test Channel",
                    "liveBroadcastContent": "none",
                },
                "contentDetails": {
                    "duration": "PT2H",
                },
                "liveStreamingDetails": {
                    "actualStartTime": "2025-01-03T10:00:00Z",
                },
            },
        ]
    }


@pytest.fixture
def sample_subscription_response():
    """Sample subscription response from YouTube API."""
    return {
        "items": [
            {
                "snippet": {
                    "title": "Tech Channel",
                    "resourceId": {"channelId": "UC_tech_123"},
                    "thumbnails": {"default": {"url": "https://example.com/tech.jpg"}},
                }
            },
            {
                "snippet": {
                    "title": "Gaming Channel",
                    "resourceId": {"channelId": "UC_gaming_456"},
                    "thumbnails": {"default": {"url": "https://example.com/gaming.jpg"}},
                }
            },
        ],
        "nextPageToken": None,
    }


@pytest.fixture
def sample_transcript_segments():
    """Sample transcript segments."""
    return [
        {"text": "Hello and welcome to the video.", "start": 0.0, "duration": 3.5},
        {"text": "Today we'll be talking about Python.", "start": 3.5, "duration": 4.0},
        {"text": "Let's get started.", "start": 7.5, "duration": 2.0},
        {"text": "First, we need to install the dependencies.", "start": 120.0, "duration": 5.0},
        {"text": "Run pip install in your terminal.", "start": 125.0, "duration": 3.5},
    ]


@pytest.fixture
def sample_supadata_response():
    """Sample response from Supadata API."""
    return {
        "text": "This is an AI-generated transcript from Supadata. It provides accurate transcription even when YouTube captions are not available.",
        "lang": "en",
        "duration": 180,
    }


@pytest.fixture
def sample_processed_video():
    """Sample ProcessedVideo data."""
    return {
        "video_id": "abc123xyz",
        "channel_id": "UC_test_channel",
        "title": "How to Build a YouTube Digest System",
        "description": "In this video, we build an automated YouTube digest system using Python, FastAPI, and Celery.",
        "duration_seconds": 1830,  # 30:30
        "published_at": "2025-01-01T14:00:00Z",
        "thumbnail_url": "https://example.com/thumbnail.jpg",
        "category": "Coding/AI Allgemein",
        "transcript": "Full transcript text goes here...",
        "transcript_source": "youtube",
        "summary": {
            "core_message": "Building a YouTube digest system helps you stay informed without spending hours watching videos.",
            "detailed_summary": "The video walks through setting up a Python-based system...",
            "key_takeaways": [
                "Use youtube-transcript-api for fetching transcripts",
                "Gemini AI provides excellent summarization",
                "Celery handles background processing efficiently",
            ],
            "timestamps": [
                {"time": "00:00", "description": "Introduction"},
                {"time": "05:30", "description": "Setting up the project"},
                {"time": "15:00", "description": "Implementing the transcript service"},
            ],
            "action_items": [
                "Set up Google Cloud Console credentials",
                "Configure Celery with Redis",
            ],
        },
    }
