"""
Unit Tests for Summarization Service

Tests cover:
- Pydantic model validation
- Chunking logic
- Duration formatting
- Error handling
- Retry logic (mocked)
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.summarization_service import (
    CHUNK_SIZE,
    MAX_TRANSCRIPT_CHARS,
    Category,
    SummarizationError,
    SummarizationService,
    SummarizationStatus,
    TimestampNote,
    VideoSummary,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def service():
    """Create a SummarizationService instance with mocked client."""
    with patch("app.services.summarization_service.settings") as mock_settings:
        mock_settings.gemini_api_key = "test-api-key"
        return SummarizationService()


@pytest.fixture
def sample_transcript():
    """Sample transcript for testing."""
    return """
    Willkommen zu diesem Video über Claude Code. Heute zeige ich euch,
    wie ihr mit dem Claude CLI eure Produktivität steigern könnt.
    Zuerst installieren wir Claude Code mit npm install -g claude.
    Dann konfigurieren wir die API-Keys und schauen uns die wichtigsten
    Befehle an. Am Ende des Videos werdet ihr wissen, wie ihr Claude
    als euren Programmier-Assistenten nutzen könnt.
    """


@pytest.fixture
def sample_video_summary_dict():
    """Sample VideoSummary as dict (as returned by Gemini)."""
    return {
        "category": "Claude Code",
        "core_message": "Dieses Video erklärt die Grundlagen von Claude Code CLI.",
        "detailed_summary": "In diesem Tutorial wird gezeigt, wie man Claude Code installiert und konfiguriert. Der Fokus liegt auf den wichtigsten Befehlen für den täglichen Einsatz.",
        "key_takeaways": [
            "Claude Code wird via npm installiert",
            "API-Keys müssen konfiguriert werden",
            "Es gibt viele nützliche Befehle für Entwickler",
        ],
        "timestamps": [
            {"time": "00:30", "description": "Installation"},
            {"time": "02:15", "description": "Konfiguration"},
        ],
        "action_items": ["Claude Code installieren", "API-Key einrichten"],
    }


# =============================================================================
# Pydantic Model Tests
# =============================================================================


class TestCategory:
    """Tests for Category enum."""

    def test_all_categories_exist(self):
        """All expected categories are defined."""
        expected = [
            "Claude Code",
            "Coding/AI Allgemein",
            "Brettspiele",
            "Gesundheit",
            "Sport",
            "Beziehung/Sexualität",
            "Beachvolleyball",
            "Sonstige",
        ]
        actual = [c.value for c in Category]
        assert actual == expected

    def test_category_from_string(self):
        """Categories can be created from string values."""
        assert Category("Claude Code") == Category.CLAUDE_CODE
        assert Category("Sonstige") == Category.SONSTIGE

    def test_invalid_category_raises(self):
        """Invalid category string raises ValueError."""
        with pytest.raises(ValueError):
            Category("Invalid Category")


class TestTimestampNote:
    """Tests for TimestampNote model."""

    def test_valid_timestamp(self):
        """Valid timestamp note can be created."""
        ts = TimestampNote(time="05:30", description="Wichtiger Punkt")
        assert ts.time == "05:30"
        assert ts.description == "Wichtiger Punkt"

    def test_timestamp_with_hours(self):
        """Timestamp with hours format works."""
        ts = TimestampNote(time="01:30:45", description="Späterer Punkt")
        assert ts.time == "01:30:45"


class TestVideoSummary:
    """Tests for VideoSummary model."""

    def test_valid_summary(self, sample_video_summary_dict):
        """Valid summary can be created from dict."""
        summary = VideoSummary.model_validate(sample_video_summary_dict)

        assert summary.category == Category.CLAUDE_CODE
        assert "Grundlagen" in summary.core_message
        assert len(summary.key_takeaways) == 3
        assert len(summary.timestamps) == 2
        assert len(summary.action_items) == 2

    def test_summary_with_empty_optionals(self):
        """Summary works without optional fields."""
        data = {
            "category": "Sonstige",
            "core_message": "Test",
            "detailed_summary": "Test Summary",
            "key_takeaways": ["Point 1"],
        }
        summary = VideoSummary.model_validate(data)

        assert summary.timestamps == []
        assert summary.action_items == []

    def test_summary_to_json(self, sample_video_summary_dict):
        """Summary can be serialized to JSON."""
        summary = VideoSummary.model_validate(sample_video_summary_dict)
        json_str = summary.model_dump_json()

        # Verify it's valid JSON
        parsed = json.loads(json_str)
        assert parsed["category"] == "Claude Code"


class TestSummarizationStatus:
    """Tests for SummarizationStatus enum."""

    def test_all_statuses_exist(self):
        """All expected statuses are defined."""
        expected = ["pending", "processing", "completed", "failed", "retry_scheduled"]
        actual = [s.value for s in SummarizationStatus]
        assert actual == expected


# =============================================================================
# Service Tests
# =============================================================================


class TestSummarizationService:
    """Tests for SummarizationService class."""

    def test_format_duration_seconds(self, service):
        """Duration formatting for seconds only."""
        assert service._format_duration(45) == "45s"

    def test_format_duration_minutes(self, service):
        """Duration formatting for minutes."""
        assert service._format_duration(125) == "2min 5s"

    def test_format_duration_hours(self, service):
        """Duration formatting for hours."""
        assert service._format_duration(3725) == "1h 2min"

    def test_chunk_transcript_short(self, service, sample_transcript):
        """Short transcript is not chunked."""
        chunks = service._chunk_transcript(sample_transcript)
        assert len(chunks) == 1
        assert chunks[0] == sample_transcript

    def test_chunk_transcript_long(self, service):
        """Long transcript is properly chunked."""
        # Create a transcript longer than MAX_TRANSCRIPT_CHARS
        long_transcript = "Test sentence. " * 50000  # ~750k chars

        chunks = service._chunk_transcript(long_transcript)

        assert len(chunks) > 1
        # Each chunk should be <= CHUNK_SIZE (plus some buffer for sentence boundary)
        for chunk in chunks:
            assert len(chunk) <= CHUNK_SIZE + 1000

    def test_chunk_transcript_preserves_content(self, service):
        """Chunking preserves most content (with overlap)."""
        # Create deterministic long transcript
        sentences = [f"This is sentence number {i}. " for i in range(10000)]
        long_transcript = "".join(sentences)

        chunks = service._chunk_transcript(long_transcript)

        # Verify first sentence is in first chunk
        assert "sentence number 0" in chunks[0]

        # Verify last sentence is in last chunk
        assert "sentence number 9999" in chunks[-1]


class TestSummarizationServiceWithMocks:
    """Tests for SummarizationService with mocked Gemini client."""

    @pytest.fixture
    def mock_service(self, sample_video_summary_dict):
        """Service with mocked Gemini client."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Mock the client
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.parsed = VideoSummary.model_validate(sample_video_summary_dict)
            mock_response.text = json.dumps(sample_video_summary_dict)

            mock_client.models.generate_content.return_value = mock_response
            service._client = mock_client

            return service

    def test_summarize_video_success(self, mock_service, sample_transcript):
        """Successful video summarization."""
        summary = mock_service.summarize_video(
            transcript=sample_transcript,
            title="Claude Code Tutorial",
            channel="Tech Channel",
            duration_seconds=600,
        )

        assert summary.category == Category.CLAUDE_CODE
        assert len(summary.key_takeaways) > 0

        # Verify API was called
        mock_service._client.models.generate_content.assert_called_once()

    def test_categorize_video_success(self, mock_service):
        """Successful video categorization."""
        # Mock for category-only response
        mock_response = MagicMock()

        class CategoryResponse:
            category = Category.CODING_AI

        mock_response.parsed = CategoryResponse()
        mock_service._client.models.generate_content.return_value = mock_response

        category = mock_service.categorize_video(
            title="Python Tutorial",
            channel="Code Channel",
            description="Learn Python programming",
        )

        assert category == Category.CODING_AI


class TestRetryLogic:
    """Tests for retry logic."""

    def test_retry_on_failure(self):
        """Service retries on failure."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Mock client that fails twice, then succeeds
            mock_client = MagicMock()
            call_count = 0

            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise Exception("API Error")

                mock_response = MagicMock()
                mock_response.parsed = VideoSummary(
                    category=Category.SONSTIGE,
                    core_message="Test",
                    detailed_summary="Test",
                    key_takeaways=["Test"],
                )
                return mock_response

            mock_client.models.generate_content.side_effect = side_effect
            service._client = mock_client

            # Patch time.sleep to speed up test
            with patch("time.sleep"):
                summary = service.summarize_video(
                    transcript="Test transcript",
                    title="Test",
                    channel="Test",
                    duration_seconds=60,
                )

            assert call_count == 3
            assert summary.category == Category.SONSTIGE

    def test_max_retries_exceeded(self):
        """Service raises error after max retries."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Mock client that always fails
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = Exception("API Error")
            service._client = mock_client

            # Patch time.sleep to speed up test
            with patch("time.sleep"):
                with pytest.raises(SummarizationError) as exc_info:
                    service.summarize_video(
                        transcript="Test transcript",
                        title="Test",
                        channel="Test",
                        duration_seconds=60,
                    )

            assert exc_info.value.retry_later is True
            assert "3 attempts" in exc_info.value.message


class TestBatchSummarize:
    """Tests for batch summarization."""

    def test_batch_summarize_with_failures(self):
        """Batch summarize handles individual failures gracefully."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Mock client that fails for second video
            mock_client = MagicMock()
            call_count = 0

            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1

                # Fail on second call (after retries exhausted)
                if call_count in [4, 5, 6]:  # Retries for video 2
                    raise Exception("API Error")

                mock_response = MagicMock()
                mock_response.parsed = VideoSummary(
                    category=Category.SONSTIGE,
                    core_message="Test",
                    detailed_summary="Test",
                    key_takeaways=["Test"],
                )
                return mock_response

            mock_client.models.generate_content.side_effect = side_effect
            service._client = mock_client

            videos = [
                {
                    "transcript": "Video 1",
                    "title": "Video 1",
                    "channel": "Ch",
                    "duration_seconds": 60,
                },
                {
                    "transcript": "Video 2",
                    "title": "Video 2",
                    "channel": "Ch",
                    "duration_seconds": 60,
                },
                {
                    "transcript": "Video 3",
                    "title": "Video 3",
                    "channel": "Ch",
                    "duration_seconds": 60,
                },
            ]

            with patch("time.sleep"):
                results = service.batch_summarize(videos)

            assert len(results) == 3

            # Video 1 succeeded
            assert results[0][1] is not None
            assert results[0][2] is None

            # Video 2 failed
            assert results[1][1] is None
            assert results[1][2] is not None

            # Video 3 succeeded
            assert results[2][1] is not None
            assert results[2][2] is None


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_transcript(self):
        """Empty transcript is handled."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Even empty transcript should be processed (Gemini will handle it)
            chunks = service._chunk_transcript("")
            assert chunks == [""]

    def test_transcript_at_boundary(self):
        """Transcript exactly at chunk boundary."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Transcript exactly at MAX_TRANSCRIPT_CHARS
            transcript = "x" * MAX_TRANSCRIPT_CHARS
            chunks = service._chunk_transcript(transcript)

            assert len(chunks) == 1

    def test_transcript_just_over_boundary(self):
        """Transcript just over chunk boundary gets chunked."""
        with patch("app.services.summarization_service.settings") as mock_settings:
            mock_settings.gemini_api_key = "test-api-key"

            service = SummarizationService()

            # Transcript just over MAX_TRANSCRIPT_CHARS
            transcript = "x" * (MAX_TRANSCRIPT_CHARS + 1)
            chunks = service._chunk_transcript(transcript)

            assert len(chunks) > 1

    def test_category_enum_serialization(self):
        """Category enum serializes correctly for JSON."""
        summary = VideoSummary(
            category=Category.CLAUDE_CODE,
            core_message="Test",
            detailed_summary="Test",
            key_takeaways=["Test"],
        )

        # Serialize to dict
        data = summary.model_dump()
        assert data["category"] == "Claude Code"

        # Serialize to JSON
        json_str = summary.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["category"] == "Claude Code"
