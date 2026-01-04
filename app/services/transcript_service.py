"""
Transcript Service for YouTube Digest

Handles:
- Fetching transcripts from YouTube using youtube-transcript-api
- Fallback to Supadata API for AI-generated transcripts
- Language detection and preference (DE > EN)
- Chunking for long videos
"""
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from app.config import settings

logger = logging.getLogger(__name__)

# Preferred languages in order
PREFERRED_LANGUAGES = ["de", "en", "en-US", "en-GB"]

# Maximum transcript length before chunking (characters)
MAX_TRANSCRIPT_LENGTH = 100_000

# Supadata API configuration
SUPADATA_BASE_URL = "https://api.supadata.ai/v1"


class TranscriptError(Exception):
    """Base exception for transcript errors."""
    pass


class TranscriptNotAvailable(TranscriptError):
    """No transcript available from any source."""
    pass


class SupadataError(TranscriptError):
    """Error from Supadata API."""
    pass


@dataclass
class TranscriptResult:
    """Result of transcript fetching."""

    video_id: str
    text: str
    language: str
    source: str  # "youtube", "youtube_auto", "supadata"
    segments: Optional[list[dict]] = None  # Optional timestamped segments

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def char_count(self) -> int:
        return len(self.text)


def format_transcript_with_timestamps(segments: list[dict]) -> str:
    """
    Format transcript segments with timestamps.

    Args:
        segments: List of {"text": str, "start": float, "duration": float}

    Returns:
        Formatted transcript with timestamps every ~2 minutes
    """
    if not segments:
        return ""

    formatted_parts = []
    last_timestamp = -120  # Start with negative to ensure first timestamp is included

    for segment in segments:
        start = segment.get("start", 0)
        text = segment.get("text", "").strip()

        if not text:
            continue

        # Add timestamp every ~2 minutes
        if start - last_timestamp >= 120:
            minutes = int(start // 60)
            seconds = int(start % 60)
            formatted_parts.append(f"\n[{minutes:02d}:{seconds:02d}] ")
            last_timestamp = start

        formatted_parts.append(text + " ")

    return "".join(formatted_parts).strip()


def format_transcript_plain(segments: list[dict]) -> str:
    """
    Format transcript as plain text without timestamps.

    Args:
        segments: List of {"text": str, "start": float, "duration": float}

    Returns:
        Plain text transcript
    """
    return " ".join(
        segment.get("text", "").strip()
        for segment in segments
        if segment.get("text", "").strip()
    )


class TranscriptService:
    """Service for fetching video transcripts."""

    def __init__(self, supadata_api_key: Optional[str] = None):
        """
        Initialize transcript service.

        Args:
            supadata_api_key: API key for Supadata fallback
        """
        self.supadata_api_key = supadata_api_key or settings.supadata_api_key
        self._http_client: Optional[httpx.Client] = None

    @property
    def http_client(self) -> httpx.Client:
        """Get or create HTTP client for Supadata."""
        if self._http_client is None:
            self._http_client = httpx.Client(
                base_url=SUPADATA_BASE_URL,
                headers={"x-api-key": self.supadata_api_key},
                timeout=60.0,
            )
        return self._http_client

    def close(self):
        """Close HTTP client."""
        if self._http_client:
            self._http_client.close()
            self._http_client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_transcript_youtube(
        self,
        video_id: str,
        languages: Optional[list[str]] = None,
    ) -> Optional[TranscriptResult]:
        """
        Fetch transcript from YouTube using youtube-transcript-api.

        Tries to get manually created transcripts first, then auto-generated.

        Args:
            video_id: YouTube video ID
            languages: List of language codes to try (in order of preference)

        Returns:
            TranscriptResult or None if not available
        """
        languages = languages or PREFERRED_LANGUAGES

        try:
            # youtube-transcript-api v1.x uses instance method
            ytt_api = YouTubeTranscriptApi()
            transcript_list = ytt_api.list(video_id)

            # Try manually created transcripts first
            for lang in languages:
                try:
                    transcript = transcript_list.find_manually_created_transcript([lang])
                    segments = transcript.fetch()

                    return TranscriptResult(
                        video_id=video_id,
                        text=format_transcript_plain(segments),
                        language=transcript.language_code,
                        source="youtube",
                        segments=segments,
                    )
                except NoTranscriptFound:
                    continue

            # Fall back to auto-generated transcripts
            for lang in languages:
                try:
                    transcript = transcript_list.find_generated_transcript([lang])
                    segments = transcript.fetch()

                    return TranscriptResult(
                        video_id=video_id,
                        text=format_transcript_plain(segments),
                        language=transcript.language_code,
                        source="youtube_auto",
                        segments=segments,
                    )
                except NoTranscriptFound:
                    continue

            # Try any available transcript and translate
            try:
                available = list(transcript_list)
                if available:
                    # Get first available transcript
                    transcript = available[0]

                    # Try to translate to German
                    if transcript.is_translatable:
                        try:
                            translated = transcript.translate("de")
                            segments = translated.fetch()
                            return TranscriptResult(
                                video_id=video_id,
                                text=format_transcript_plain(segments),
                                language="de",
                                source="youtube_auto",
                                segments=segments,
                            )
                        except Exception:
                            pass

                    # Use original if translation fails
                    segments = transcript.fetch()
                    return TranscriptResult(
                        video_id=video_id,
                        text=format_transcript_plain(segments),
                        language=transcript.language_code,
                        source="youtube_auto",
                        segments=segments,
                    )
            except Exception:
                pass

            logger.info(f"No transcript found on YouTube for {video_id}")
            return None

        except TranscriptsDisabled:
            logger.info(f"Transcripts disabled for video {video_id}")
            return None
        except VideoUnavailable:
            logger.warning(f"Video unavailable: {video_id}")
            return None
        except Exception as e:
            logger.warning(f"YouTube transcript error for {video_id}: {e}")
            return None

    def get_transcript_supadata(self, video_id: str) -> Optional[TranscriptResult]:
        """
        Fetch transcript from Supadata API (AI transcription).

        Args:
            video_id: YouTube video ID

        Returns:
            TranscriptResult or None if not available
        """
        if not self.supadata_api_key:
            logger.warning("Supadata API key not configured")
            return None

        try:
            response = self.http_client.get(
                "/youtube/transcript",
                params={"videoId": video_id, "text": "true"},
            )

            if response.status_code == 404:
                logger.info(f"Supadata: No transcript for {video_id}")
                return None

            if response.status_code == 429:
                logger.warning("Supadata: Rate limit exceeded")
                raise SupadataError("Rate limit exceeded")

            response.raise_for_status()
            data = response.json()

            # Supadata returns {"content": [{"text": "...", "start": 0, ...}]}
            # or {"text": "..."} for text-only mode
            if "text" in data:
                return TranscriptResult(
                    video_id=video_id,
                    text=data["text"],
                    language=data.get("lang", "unknown"),
                    source="supadata",
                    segments=None,
                )

            content = data.get("content", [])
            if content:
                return TranscriptResult(
                    video_id=video_id,
                    text=format_transcript_plain(content),
                    language=data.get("lang", "unknown"),
                    source="supadata",
                    segments=content,
                )

            return None

        except SupadataError:
            # Re-raise our own errors
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"Supadata HTTP error for {video_id}: {e}")
            raise SupadataError(f"HTTP error: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Supadata request error for {video_id}: {e}")
            raise SupadataError(f"Request error: {e}")
        except Exception as e:
            logger.error(f"Supadata error for {video_id}: {e}")
            return None

    def get_transcript(
        self,
        video_id: str,
        use_fallback: bool = True,
        include_timestamps: bool = False,
    ) -> TranscriptResult:
        """
        Get transcript for a video, trying YouTube first, then Supadata.

        Args:
            video_id: YouTube video ID
            use_fallback: Whether to try Supadata if YouTube fails
            include_timestamps: Whether to include timestamps in text

        Returns:
            TranscriptResult with transcript text

        Raises:
            TranscriptNotAvailable: If no transcript could be obtained
        """
        # Try YouTube first
        result = self.get_transcript_youtube(video_id)

        if result:
            logger.info(
                f"Got transcript for {video_id} from {result.source} "
                f"({result.word_count} words, {result.language})"
            )

            # Format with timestamps if requested and segments available
            if include_timestamps and result.segments:
                result.text = format_transcript_with_timestamps(result.segments)

            return result

        # Try Supadata fallback
        if use_fallback:
            logger.info(f"Trying Supadata fallback for {video_id}")
            result = self.get_transcript_supadata(video_id)

            if result:
                logger.info(
                    f"Got transcript for {video_id} from Supadata "
                    f"({result.word_count} words)"
                )

                if include_timestamps and result.segments:
                    result.text = format_transcript_with_timestamps(result.segments)

                return result

        raise TranscriptNotAvailable(
            f"No transcript available for video {video_id}"
        )

    def chunk_transcript(
        self,
        transcript: str,
        max_length: int = MAX_TRANSCRIPT_LENGTH,
        overlap: int = 500,
    ) -> list[str]:
        """
        Split a long transcript into chunks for processing.

        Useful for very long videos that exceed LLM context limits.

        Args:
            transcript: Full transcript text
            max_length: Maximum characters per chunk
            overlap: Overlap between chunks for context

        Returns:
            List of transcript chunks
        """
        if len(transcript) <= max_length:
            return [transcript]

        chunks = []
        start = 0

        while start < len(transcript):
            end = start + max_length

            # Try to break at a sentence boundary
            if end < len(transcript):
                # Look for sentence end in the last 20% of the chunk
                search_start = start + int(max_length * 0.8)
                sentence_end = transcript.rfind(". ", search_start, end)

                if sentence_end > search_start:
                    end = sentence_end + 1

            chunks.append(transcript[start:end].strip())
            start = end - overlap

        logger.info(
            f"Split transcript into {len(chunks)} chunks "
            f"(original: {len(transcript)} chars)"
        )
        return chunks


# CLI for testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transcript Service CLI")
    parser.add_argument(
        "video_id",
        help="YouTube video ID to fetch transcript for",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Don't use Supadata fallback",
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Include timestamps in output",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    with TranscriptService() as service:
        try:
            result = service.get_transcript(
                args.video_id,
                use_fallback=not args.no_fallback,
                include_timestamps=args.timestamps,
            )

            print(f"\n{'='*60}")
            print(f"Video ID: {result.video_id}")
            print(f"Source: {result.source}")
            print(f"Language: {result.language}")
            print(f"Words: {result.word_count}")
            print(f"{'='*60}\n")

            # Print first 2000 characters
            if len(result.text) > 2000:
                print(result.text[:2000])
                print(f"\n... ({len(result.text) - 2000} more characters)")
            else:
                print(result.text)

        except TranscriptNotAvailable as e:
            print(f"Error: {e}")
