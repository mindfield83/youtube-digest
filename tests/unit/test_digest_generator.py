"""
Unit tests for Digest Generator Service.
"""
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.services.digest_generator import (
    CATEGORY_PRIORITY,
    DASHBOARD_BASE_URL,
    DigestGenerationError,
    DigestGenerator,
    DigestResult,
    VideoDigestItem,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def digest_generator():
    """Create a DigestGenerator instance."""
    return DigestGenerator()


@pytest.fixture
def mock_channel():
    """Create a mock Channel object."""
    channel = Mock()
    channel.channel_name = "Test Channel"
    channel.channel_id = "UC_test_123"
    return channel


@pytest.fixture
def mock_video(mock_channel):
    """Create a mock ProcessedVideo object."""
    video = Mock()
    video.video_id = "test_video_123"
    video.title = "Test Video Title"
    video.channel = mock_channel
    video.duration_seconds = 930  # 15:30
    video.duration_formatted = "15:30"
    video.published_at = datetime(2026, 1, 1, 12, 0, 0)
    video.category = "Coding/AI Allgemein"
    video.youtube_url = "https://www.youtube.com/watch?v=test_video_123"
    video.summary = {
        "core_message": "This is the core message of the video.",
        "detailed_summary": "This is a detailed summary with multiple paragraphs.",
        "key_takeaways": [
            "First key takeaway",
            "Second key takeaway",
            "Third key takeaway",
        ],
        "timestamps": [
            {"time": "00:00", "description": "Introduction"},
            {"time": "05:00", "description": "Main content"},
        ],
        "action_items": [
            "Do this first",
            "Then do this",
        ],
    }
    return video


@pytest.fixture
def mock_videos_multiple_categories(mock_channel):
    """Create mock videos across multiple categories."""
    videos = []

    # Claude Code video (highest priority)
    v1 = Mock()
    v1.video_id = "claude_video_1"
    v1.title = "Claude Code Tutorial"
    v1.channel = mock_channel
    v1.duration_seconds = 1200
    v1.duration_formatted = "20:00"
    v1.published_at = datetime(2026, 1, 3, 10, 0, 0)
    v1.category = "Claude Code"
    v1.youtube_url = "https://www.youtube.com/watch?v=claude_video_1"
    v1.summary = {
        "core_message": "Learn Claude Code basics.",
        "key_takeaways": ["Use / commands", "Understand context"],
        "action_items": [],
    }
    videos.append(v1)

    # Coding/AI video
    v2 = Mock()
    v2.video_id = "coding_video_1"
    v2.title = "Python Best Practices"
    v2.channel = mock_channel
    v2.duration_seconds = 900
    v2.duration_formatted = "15:00"
    v2.published_at = datetime(2026, 1, 2, 10, 0, 0)
    v2.category = "Coding/AI Allgemein"
    v2.youtube_url = "https://www.youtube.com/watch?v=coding_video_1"
    v2.summary = {
        "core_message": "Python best practices for 2026.",
        "key_takeaways": ["Use type hints", "Write tests"],
        "action_items": ["Refactor old code"],
    }
    videos.append(v2)

    # Sonstige video (lowest priority)
    v3 = Mock()
    v3.video_id = "other_video_1"
    v3.title = "Random Topic"
    v3.channel = mock_channel
    v3.duration_seconds = 600
    v3.duration_formatted = "10:00"
    v3.published_at = datetime(2026, 1, 1, 10, 0, 0)
    v3.category = "Sonstige"
    v3.youtube_url = "https://www.youtube.com/watch?v=other_video_1"
    v3.summary = {
        "core_message": "Miscellaneous content.",
        "key_takeaways": ["Interesting fact"],
        "action_items": [],
    }
    videos.append(v3)

    return videos


# =============================================================================
# VideoDigestItem Preparation Tests
# =============================================================================


class TestPrepareVideoItem:
    """Tests for _prepare_video_item method."""

    def test_prepare_video_item_success(self, digest_generator, mock_video):
        """Test successful video item preparation."""
        item = digest_generator._prepare_video_item(mock_video)

        assert item is not None
        assert isinstance(item, VideoDigestItem)
        assert item.video_id == "test_video_123"
        assert item.title == "Test Video Title"
        assert item.channel_name == "Test Channel"
        assert item.duration == "15:30"
        assert item.category == "Coding/AI Allgemein"
        assert item.core_message == "This is the core message of the video."
        assert len(item.key_takeaways) == 3
        assert len(item.action_items) == 2
        assert item.youtube_url == "https://www.youtube.com/watch?v=test_video_123"
        assert item.summary_url == f"{DASHBOARD_BASE_URL}/video/test_video_123"

    def test_prepare_video_item_no_summary(self, digest_generator, mock_video):
        """Test that videos without summary are skipped."""
        mock_video.summary = None
        item = digest_generator._prepare_video_item(mock_video)
        assert item is None

    def test_prepare_video_item_empty_core_message(self, digest_generator, mock_video):
        """Test that videos with empty core_message are skipped."""
        mock_video.summary = {"core_message": "", "key_takeaways": []}
        item = digest_generator._prepare_video_item(mock_video)
        assert item is None

    def test_prepare_video_item_no_channel(self, digest_generator, mock_video):
        """Test handling of video without channel relationship."""
        mock_video.channel = None
        item = digest_generator._prepare_video_item(mock_video)

        assert item is not None
        assert item.channel_name == "Unbekannt"

    def test_prepare_video_item_limits_takeaways(self, digest_generator, mock_video):
        """Test that key takeaways are limited to 10."""
        mock_video.summary["key_takeaways"] = [f"Takeaway {i}" for i in range(15)]
        item = digest_generator._prepare_video_item(mock_video)

        assert len(item.key_takeaways) == 10

    def test_prepare_video_item_limits_action_items(self, digest_generator, mock_video):
        """Test that action items are limited to 5."""
        mock_video.summary["action_items"] = [f"Action {i}" for i in range(10)]
        item = digest_generator._prepare_video_item(mock_video)

        assert len(item.action_items) == 5


# =============================================================================
# Category Grouping Tests
# =============================================================================


class TestCategoryGrouping:
    """Tests for _group_by_category method."""

    def test_group_by_category_priority_order(self, digest_generator):
        """Test that categories are sorted by priority."""
        videos = [
            VideoDigestItem(
                video_id="1",
                title="Test",
                channel_name="Ch",
                duration="10:00",
                published_at=datetime(2026, 1, 1),
                category="Sonstige",
                core_message="Msg",
                key_takeaways=[],
                action_items=[],
                youtube_url="",
                summary_url="",
            ),
            VideoDigestItem(
                video_id="2",
                title="Test",
                channel_name="Ch",
                duration="10:00",
                published_at=datetime(2026, 1, 1),
                category="Claude Code",
                core_message="Msg",
                key_takeaways=[],
                action_items=[],
                youtube_url="",
                summary_url="",
            ),
            VideoDigestItem(
                video_id="3",
                title="Test",
                channel_name="Ch",
                duration="10:00",
                published_at=datetime(2026, 1, 1),
                category="Coding/AI Allgemein",
                core_message="Msg",
                key_takeaways=[],
                action_items=[],
                youtube_url="",
                summary_url="",
            ),
        ]

        grouped = digest_generator._group_by_category(videos)
        categories = list(grouped.keys())

        assert categories[0] == "Claude Code"
        assert categories[1] == "Coding/AI Allgemein"
        assert categories[2] == "Sonstige"

    def test_group_by_category_videos_sorted_by_date(self, digest_generator):
        """Test that videos within categories are sorted by published date (newest first)."""
        videos = [
            VideoDigestItem(
                video_id="1",
                title="Older",
                channel_name="Ch",
                duration="10:00",
                published_at=datetime(2026, 1, 1),
                category="Coding/AI Allgemein",
                core_message="Msg",
                key_takeaways=[],
                action_items=[],
                youtube_url="",
                summary_url="",
            ),
            VideoDigestItem(
                video_id="2",
                title="Newer",
                channel_name="Ch",
                duration="10:00",
                published_at=datetime(2026, 1, 5),
                category="Coding/AI Allgemein",
                core_message="Msg",
                key_takeaways=[],
                action_items=[],
                youtube_url="",
                summary_url="",
            ),
        ]

        grouped = digest_generator._group_by_category(videos)
        coding_videos = grouped["Coding/AI Allgemein"]

        assert coding_videos[0].title == "Newer"
        assert coding_videos[1].title == "Older"


# =============================================================================
# Statistics Tests
# =============================================================================


class TestCalculateStats:
    """Tests for _calculate_stats method."""

    def test_calculate_stats_total_duration(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test total duration calculation."""
        total_duration, _ = digest_generator._calculate_stats(
            mock_videos_multiple_categories
        )

        expected = 1200 + 900 + 600  # 2700 seconds
        assert total_duration == expected

    def test_calculate_stats_category_counts(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test category count calculation."""
        _, category_counts = digest_generator._calculate_stats(
            mock_videos_multiple_categories
        )

        assert category_counts["Claude Code"] == 1
        assert category_counts["Coding/AI Allgemein"] == 1
        assert category_counts["Sonstige"] == 1


# =============================================================================
# Duration Formatting Tests
# =============================================================================


class TestFormatDuration:
    """Tests for _format_duration method."""

    def test_format_duration_hours_and_minutes(self, digest_generator):
        """Test formatting with hours and minutes."""
        result = digest_generator._format_duration(7200 + 1800)  # 2h 30min
        assert result == "2h 30min"

    def test_format_duration_minutes_only(self, digest_generator):
        """Test formatting with minutes only."""
        result = digest_generator._format_duration(900)  # 15min
        assert result == "15min"

    def test_format_duration_zero(self, digest_generator):
        """Test formatting zero duration."""
        result = digest_generator._format_duration(0)
        assert result == "0min"


# =============================================================================
# Full Generation Tests
# =============================================================================


class TestGenerate:
    """Tests for generate method."""

    def test_generate_empty_videos_raises_error(self, digest_generator):
        """Test that empty video list raises error."""
        with pytest.raises(DigestGenerationError, match="No videos provided"):
            digest_generator.generate([])

    def test_generate_all_videos_without_summary_raises_error(
        self, digest_generator, mock_video
    ):
        """Test that all videos without summaries raises error."""
        mock_video.summary = None

        with pytest.raises(DigestGenerationError, match="No videos with valid summaries"):
            digest_generator.generate([mock_video])

    def test_generate_success(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test successful digest generation."""
        result = digest_generator.generate(mock_videos_multiple_categories)

        assert isinstance(result, DigestResult)
        assert result.video_count == 3
        assert result.total_duration_seconds == 2700
        assert len(result.category_counts) == 3
        assert "Claude Code" in result.category_counts
        assert result.html is not None
        assert result.plain_text is not None
        assert "YouTube Digest" in result.subject

    def test_generate_html_contains_video_titles(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test that generated HTML contains video titles."""
        result = digest_generator.generate(mock_videos_multiple_categories)

        assert "Claude Code Tutorial" in result.html
        assert "Python Best Practices" in result.html
        assert "Random Topic" in result.html

    def test_generate_html_contains_category_headers(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test that generated HTML contains category headers."""
        result = digest_generator.generate(mock_videos_multiple_categories)

        assert "Claude Code" in result.html
        assert "Coding/AI Allgemein" in result.html
        assert "Sonstige" in result.html

    def test_generate_plain_text_contains_videos(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test that generated plain text contains video info."""
        result = digest_generator.generate(mock_videos_multiple_categories)

        assert "Claude Code Tutorial" in result.plain_text
        assert "youtube.com/watch" in result.plain_text
        assert "Key Takeaways" in result.plain_text

    def test_generate_with_custom_period(
        self, digest_generator, mock_videos_multiple_categories
    ):
        """Test generation with custom period dates."""
        period_start = datetime(2026, 1, 1)
        period_end = datetime(2026, 1, 15)

        result = digest_generator.generate(
            mock_videos_multiple_categories,
            period_start=period_start,
            period_end=period_end,
        )

        assert result.period_start == period_start
        assert result.period_end == period_end
        assert "01.01.2026" in result.subject
        assert "15.01.2026" in result.subject


# =============================================================================
# Category Priority Tests
# =============================================================================


class TestCategoryPriority:
    """Tests for category priority constants."""

    def test_claude_code_highest_priority(self):
        """Test that Claude Code has highest priority (0)."""
        assert CATEGORY_PRIORITY["Claude Code"] == 0

    def test_coding_ai_second_priority(self):
        """Test that Coding/AI Allgemein has second priority."""
        assert CATEGORY_PRIORITY["Coding/AI Allgemein"] == 1

    def test_sonstige_lowest_priority(self):
        """Test that Sonstige has lowest priority."""
        assert CATEGORY_PRIORITY["Sonstige"] == 99

    def test_all_categories_have_priority(self):
        """Test that all expected categories have a priority."""
        expected_categories = [
            "Claude Code",
            "Coding/AI Allgemein",
            "Brettspiele",
            "Gesundheit",
            "Sport",
            "Beziehung/Sexualität",
            "Beachvolleyball",
            "Sonstige",
        ]
        for cat in expected_categories:
            assert cat in CATEGORY_PRIORITY


