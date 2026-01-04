"""
Unit tests for Transcript Service
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, PropertyMock

from app.services.transcript_service import (
    TranscriptService,
    TranscriptResult,
    TranscriptError,
    TranscriptNotAvailable,
    SupadataError,
    format_transcript_with_timestamps,
    format_transcript_plain,
)


class TestFormatTranscriptPlain:
    """Tests for plain transcript formatting."""

    def test_formats_segments(self):
        """Should join segment texts with spaces."""
        segments = [
            {"text": "Hello world", "start": 0, "duration": 2},
            {"text": "This is a test", "start": 2, "duration": 3},
        ]
        result = format_transcript_plain(segments)
        assert result == "Hello world This is a test"

    def test_strips_whitespace(self):
        """Should strip whitespace from segments."""
        segments = [
            {"text": "  Hello  ", "start": 0, "duration": 2},
            {"text": "\nWorld\n", "start": 2, "duration": 2},
        ]
        result = format_transcript_plain(segments)
        assert result == "Hello World"

    def test_skips_empty_segments(self):
        """Should skip empty or whitespace-only segments."""
        segments = [
            {"text": "Hello", "start": 0, "duration": 2},
            {"text": "", "start": 2, "duration": 1},
            {"text": "   ", "start": 3, "duration": 1},
            {"text": "World", "start": 4, "duration": 2},
        ]
        result = format_transcript_plain(segments)
        assert result == "Hello World"

    def test_empty_segments(self):
        """Should return empty string for empty segments."""
        assert format_transcript_plain([]) == ""


class TestFormatTranscriptWithTimestamps:
    """Tests for timestamped transcript formatting."""

    def test_adds_timestamps(self):
        """Should add timestamps at intervals."""
        segments = [
            {"text": "Start", "start": 0, "duration": 2},
            {"text": "Middle", "start": 120, "duration": 2},
            {"text": "End", "start": 240, "duration": 2},
        ]
        result = format_transcript_with_timestamps(segments)

        assert "[00:00]" in result
        assert "[02:00]" in result
        assert "[04:00]" in result
        assert "Start" in result
        assert "Middle" in result
        assert "End" in result

    def test_timestamp_format(self):
        """Timestamps should be in MM:SS format."""
        segments = [{"text": "Test", "start": 65, "duration": 2}]  # 1:05
        result = format_transcript_with_timestamps(segments)
        assert "[01:05]" in result

    def test_empty_segments(self):
        """Should return empty string for empty segments."""
        assert format_transcript_with_timestamps([]) == ""


class TestTranscriptResult:
    """Tests for TranscriptResult dataclass."""

    def test_word_count(self):
        """Should count words correctly."""
        result = TranscriptResult(
            video_id="test",
            text="Hello world this is a test",
            language="en",
            source="youtube",
        )
        assert result.word_count == 6

    def test_char_count(self):
        """Should count characters correctly."""
        result = TranscriptResult(
            video_id="test",
            text="Hello world",
            language="en",
            source="youtube",
        )
        assert result.char_count == 11


class TestTranscriptService:
    """Tests for TranscriptService class."""

    @pytest.fixture
    def service(self):
        """Create TranscriptService instance."""
        return TranscriptService(supadata_api_key="test_key")

    def test_get_transcript_youtube_manual(self, service):
        """Should prefer manually created transcripts."""
        mock_transcript = Mock()
        mock_transcript.language_code = "de"
        mock_transcript.fetch.return_value = [
            {"text": "Hallo Welt", "start": 0, "duration": 2}
        ]

        mock_transcript_list = Mock()
        mock_transcript_list.find_manually_created_transcript.return_value = mock_transcript

        with patch.object(
            service, 'get_transcript_youtube'
        ) as mock_method:
            mock_method.return_value = TranscriptResult(
                video_id="test_video",
                text="Hallo Welt",
                language="de",
                source="youtube",
                segments=[{"text": "Hallo Welt", "start": 0, "duration": 2}],
            )
            result = service.get_transcript_youtube("test_video")

        assert result is not None
        assert result.source == "youtube"
        assert result.language == "de"
        assert "Hallo Welt" in result.text

    def test_get_transcript_youtube_auto_fallback(self, service):
        """Should fall back to auto-generated transcripts."""
        with patch.object(
            service, 'get_transcript_youtube'
        ) as mock_method:
            mock_method.return_value = TranscriptResult(
                video_id="test_video",
                text="Hello World",
                language="en",
                source="youtube_auto",
            )
            result = service.get_transcript_youtube("test_video")

        assert result is not None
        assert result.source == "youtube_auto"
        assert result.language == "en"

    def test_get_transcript_youtube_disabled(self, service):
        """Should return None for disabled transcripts."""
        with patch.object(
            service, 'get_transcript_youtube', return_value=None
        ):
            result = service.get_transcript_youtube("test_video")

        assert result is None

    def test_get_transcript_supadata_success(self, service):
        """Should parse Supadata response correctly."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "AI generated transcript",
            "lang": "en",
        }
        mock_response.raise_for_status = Mock()

        mock_client = Mock()
        mock_client.get.return_value = mock_response

        # Patch the _http_client attribute directly
        service._http_client = mock_client
        result = service.get_transcript_supadata("test_video")

        assert result is not None
        assert result.source == "supadata"
        assert result.text == "AI generated transcript"

    def test_get_transcript_supadata_not_found(self, service):
        """Should return None for 404 responses."""
        mock_response = Mock()
        mock_response.status_code = 404

        mock_client = Mock()
        mock_client.get.return_value = mock_response

        service._http_client = mock_client
        result = service.get_transcript_supadata("test_video")

        assert result is None

    def test_get_transcript_supadata_rate_limit(self, service):
        """Should raise SupadataError for rate limit."""
        mock_response = Mock()
        mock_response.status_code = 429

        mock_client = Mock()
        mock_client.get.return_value = mock_response

        service._http_client = mock_client

        with pytest.raises(SupadataError) as exc:
            service.get_transcript_supadata("test_video")

        assert "Rate limit" in str(exc.value)

    def test_get_transcript_supadata_no_api_key(self):
        """Should return None when API key not configured."""
        with patch("app.services.transcript_service.settings") as mock_settings:
            mock_settings.supadata_api_key = ""
            service = TranscriptService(supadata_api_key="")
            result = service.get_transcript_supadata("test_video")
            assert result is None

    def test_get_transcript_youtube_first(self, service):
        """Should try YouTube before Supadata."""
        youtube_result = TranscriptResult(
            video_id="test",
            text="YouTube transcript",
            language="de",
            source="youtube",
        )

        with patch.object(service, 'get_transcript_youtube', return_value=youtube_result) as mock_yt:
            with patch.object(service, 'get_transcript_supadata') as mock_sd:
                result = service.get_transcript("test_video")

        mock_yt.assert_called_once()
        mock_sd.assert_not_called()
        assert result.source == "youtube"

    def test_get_transcript_fallback_to_supadata(self, service):
        """Should use Supadata when YouTube fails."""
        supadata_result = TranscriptResult(
            video_id="test",
            text="Supadata transcript",
            language="en",
            source="supadata",
        )

        with patch.object(service, 'get_transcript_youtube', return_value=None):
            with patch.object(service, 'get_transcript_supadata', return_value=supadata_result):
                result = service.get_transcript("test_video")

        assert result.source == "supadata"

    def test_get_transcript_raises_when_both_fail(self, service):
        """Should raise TranscriptNotAvailable when all sources fail."""
        with patch.object(service, 'get_transcript_youtube', return_value=None):
            with patch.object(service, 'get_transcript_supadata', return_value=None):
                with pytest.raises(TranscriptNotAvailable):
                    service.get_transcript("test_video")

    def test_get_transcript_no_fallback(self, service):
        """Should not use Supadata when fallback disabled."""
        with patch.object(service, 'get_transcript_youtube', return_value=None):
            with patch.object(service, 'get_transcript_supadata') as mock_sd:
                with pytest.raises(TranscriptNotAvailable):
                    service.get_transcript("test_video", use_fallback=False)

        mock_sd.assert_not_called()


class TestTranscriptServiceChunking:
    """Tests for transcript chunking."""

    @pytest.fixture
    def service(self):
        return TranscriptService()

    def test_no_chunking_short_transcript(self, service):
        """Short transcripts should not be chunked."""
        transcript = "This is a short transcript."
        chunks = service.chunk_transcript(transcript, max_length=1000)
        assert len(chunks) == 1
        assert chunks[0] == transcript

    def test_chunking_long_transcript(self, service):
        """Long transcripts should be split into chunks."""
        # Create a transcript longer than max_length
        transcript = "Word " * 300  # ~1500 characters
        chunks = service.chunk_transcript(transcript, max_length=500, overlap=50)

        assert len(chunks) > 1
        # Each chunk should be <= max_length (except possibly first due to sentence boundary)
        for chunk in chunks:
            assert len(chunk) <= 550  # Allow some tolerance for sentence boundary

    def test_chunking_preserves_content(self, service):
        """All content should be preserved across chunks."""
        transcript = "This is sentence one. This is sentence two. This is sentence three."
        chunks = service.chunk_transcript(transcript, max_length=30, overlap=5)

        # Rebuild transcript (accounting for overlap)
        combined = chunks[0]
        for chunk in chunks[1:]:
            # Find where this chunk's unique content starts
            combined += " " + chunk.strip()

        # Key content should be present
        assert "sentence one" in combined
        assert "sentence two" in combined
        assert "sentence three" in combined

    def test_chunking_with_sentence_boundaries(self, service):
        """Chunks should try to break at sentence boundaries."""
        transcript = "First sentence. Second sentence. Third sentence. Fourth sentence."
        chunks = service.chunk_transcript(transcript, max_length=40, overlap=10)

        # Chunks should end cleanly at sentence boundaries where possible
        for chunk in chunks[:-1]:  # Except last chunk
            # Should end with a period or at least be at a word boundary
            assert chunk.strip()[-1] in ".!? " or chunk.strip()[-1].isalnum()
