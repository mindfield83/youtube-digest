"""
Summarization Service for YouTube Digest

Handles:
- Video summarization using Google Gemini 3.0 Flash
- Structured output with Pydantic models
- Automatic categorization of videos
- Chunking and synthesis for long videos
- Retry logic with exponential backoff
"""
import logging
import time
from enum import Enum
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

# Model configuration
GEMINI_MODEL = "gemini-3-flash-preview"

# Chunking configuration
MAX_TRANSCRIPT_CHARS = 500_000  # ~125k tokens
CHUNK_SIZE = 400_000
CHUNK_OVERLAP = 500

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds


# =============================================================================
# Pydantic Models for Structured Output
# =============================================================================


class Category(str, Enum):
    """Video categories for digest organization."""

    CLAUDE_CODE = "Claude Code"
    CODING_AI = "Coding/AI Allgemein"
    BRETTSPIELE = "Brettspiele"
    GESUNDHEIT = "Gesundheit"
    SPORT = "Sport"
    BEZIEHUNG = "Beziehung/Sexualität"
    BEACHVOLLEYBALL = "Beachvolleyball"
    SONSTIGE = "Sonstige"


class TimestampNote(BaseModel):
    """A notable timestamp in the video."""

    time: str = Field(description="Timestamp im Format MM:SS oder HH:MM:SS")
    description: str = Field(description="Kurze Beschreibung was an dieser Stelle passiert")


class VideoSummary(BaseModel):
    """Structured summary of a video."""

    category: Category = Field(description="Die passendste Kategorie für dieses Video")
    core_message: str = Field(description="Kernaussage des Videos in 2-3 Sätzen")
    detailed_summary: str = Field(description="Ausführliche Zusammenfassung in 3-5 Absätzen")
    key_takeaways: list[str] = Field(description="Die wichtigsten Erkenntnisse als Liste")
    timestamps: list[TimestampNote] = Field(
        default_factory=list,
        description="Wichtige Stellen im Video mit Zeitstempel",
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="Konkrete Handlungsempfehlungen (falls relevant)",
    )


class SummarizationStatus(str, Enum):
    """Processing status for videos."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"


# =============================================================================
# Exceptions
# =============================================================================


class SummarizationError(Exception):
    """Error during summarization."""

    def __init__(self, message: str, retry_later: bool = False):
        self.message = message
        self.retry_later = retry_later
        super().__init__(message)


# =============================================================================
# Prompts
# =============================================================================

SUMMARIZATION_PROMPT = """Du bist ein Experte für die Zusammenfassung von YouTube-Videos.

Analysiere das folgende Video und erstelle eine ausführliche deutsche Zusammenfassung.

## Video-Informationen
- **Titel:** {title}
- **Kanal:** {channel}
- **Dauer:** {duration}

## Transkript
{transcript}

## Aufgabe
Erstelle eine strukturierte Zusammenfassung mit:

1. **Kategorie**: Wähle die passendste aus:
   - "Claude Code" (Videos über Claude, Anthropic, Claude Code CLI)
   - "Coding/AI Allgemein" (Programmierung, KI, Tech, Software-Entwicklung)
   - "Brettspiele" (Tabletop, Kartenspiele, Gesellschaftsspiele)
   - "Gesundheit" (Medizin, Ernährung, Mental Health, Wellness)
   - "Sport" (Fitness, Training, allgemeiner Sport)
   - "Beziehung/Sexualität" (Partnerschaft, Dating, Intimität)
   - "Beachvolleyball" (speziell Beachvolleyball-Inhalte)
   - "Sonstige" (alles andere)

2. **Kernaussage**: 2-3 Sätze, die das Video auf den Punkt bringen

3. **Detaillierte Zusammenfassung**: 3-5 Absätze mit den wichtigsten Inhalten

4. **Key Takeaways**: Die wichtigsten Erkenntnisse als Bullet Points (5-10 Punkte)

5. **Timestamps**: Wichtige Stellen im Video (Format: "MM:SS" oder "HH:MM:SS")

6. **Action Items**: Konkrete Handlungsempfehlungen (falls das Video welche enthält)

Antworte auf Deutsch, auch wenn das Transkript auf Englisch ist."""


SYNTHESIS_PROMPT = """Du hast mehrere Teil-Zusammenfassungen eines langen Videos erhalten.
Kombiniere diese zu einer kohärenten Gesamtzusammenfassung.

## Video-Informationen
- **Titel:** {title}
- **Kanal:** {channel}
- **Dauer:** {duration}

## Teil-Zusammenfassungen
{chunk_summaries}

## Aufgabe
Erstelle eine einheitliche Zusammenfassung, die:
- Die wichtigsten Punkte aus allen Teilen kombiniert
- Redundanzen entfernt
- Eine kohärente Gesamtdarstellung bietet
- Die gleiche Struktur wie Einzelzusammenfassungen hat

Antworte auf Deutsch."""


CATEGORIZATION_PROMPT = """Kategorisiere dieses YouTube-Video basierend auf Titel und Beschreibung.

## Video-Informationen
- **Titel:** {title}
- **Kanal:** {channel}
- **Beschreibung:** {description}

## Kategorien
Wähle die passendste Kategorie:
- "Claude Code" (Videos über Claude, Anthropic, Claude Code CLI)
- "Coding/AI Allgemein" (Programmierung, KI, Tech)
- "Brettspiele" (Tabletop, Kartenspiele)
- "Gesundheit" (Medizin, Ernährung, Mental Health)
- "Sport" (Fitness, Training)
- "Beziehung/Sexualität" (Partnerschaft, Dating)
- "Beachvolleyball" (speziell Beachvolleyball)
- "Sonstige" (alles andere)"""


# =============================================================================
# Service Class
# =============================================================================


class SummarizationService:
    """Service for summarizing YouTube videos using Gemini 3.0 Flash."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the summarization service.

        Args:
            api_key: Gemini API key (uses settings if not provided)
        """
        self.api_key = api_key or settings.gemini_api_key
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        """Get or create Gemini client."""
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _format_duration(self, seconds: int) -> str:
        """Format duration in seconds to human-readable string."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}min"
        elif minutes > 0:
            return f"{minutes}min {secs}s"
        else:
            return f"{secs}s"

    def _call_gemini_with_retry(
        self,
        prompt: str,
        response_schema: type[BaseModel],
    ) -> BaseModel:
        """
        Call Gemini API with retry logic.

        Args:
            prompt: The prompt to send
            response_schema: Pydantic model for structured output

        Returns:
            Parsed response matching the schema

        Raises:
            SummarizationError: If all retries fail
        """
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                )

                # Parse and return the response
                if response.parsed:
                    return response.parsed

                # Fallback: try to parse text manually if parsed is None
                if response.text:
                    import json

                    data = json.loads(response.text)
                    return response_schema.model_validate(data)

                raise SummarizationError("Empty response from Gemini")

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Gemini API attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                )

                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)

        # All retries exhausted
        error_msg = f"Gemini API failed after {MAX_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        raise SummarizationError(error_msg, retry_later=True)

    def _chunk_transcript(self, transcript: str) -> list[str]:
        """
        Split a long transcript into chunks for processing.

        Args:
            transcript: Full transcript text

        Returns:
            List of transcript chunks
        """
        if len(transcript) <= MAX_TRANSCRIPT_CHARS:
            return [transcript]

        chunks = []
        start = 0

        while start < len(transcript):
            end = start + CHUNK_SIZE

            # Try to break at a sentence boundary
            if end < len(transcript):
                # Look for sentence end in the last 20% of the chunk
                search_start = start + int(CHUNK_SIZE * 0.8)
                sentence_end = transcript.rfind(". ", search_start, end)

                if sentence_end > search_start:
                    end = sentence_end + 1

            chunks.append(transcript[start:end].strip())
            start = end - CHUNK_OVERLAP

        logger.info(
            f"Split transcript into {len(chunks)} chunks "
            f"(original: {len(transcript)} chars)"
        )
        return chunks

    def summarize_video(
        self,
        transcript: str,
        title: str,
        channel: str,
        duration_seconds: int,
    ) -> VideoSummary:
        """
        Create a structured summary of a video.

        Args:
            transcript: Video transcript text
            title: Video title
            channel: Channel name
            duration_seconds: Video duration in seconds

        Returns:
            VideoSummary with all fields populated

        Raises:
            SummarizationError: If summarization fails
        """
        duration_str = self._format_duration(duration_seconds)

        # Check if chunking is needed
        chunks = self._chunk_transcript(transcript)

        if len(chunks) == 1:
            # Single chunk - straightforward summarization
            prompt = SUMMARIZATION_PROMPT.format(
                title=title,
                channel=channel,
                duration=duration_str,
                transcript=transcript,
            )

            logger.info(f"Summarizing video: {title} ({len(transcript)} chars)")
            return self._call_gemini_with_retry(prompt, VideoSummary)

        else:
            # Multiple chunks - summarize each, then synthesize
            logger.info(
                f"Video requires chunking: {title} "
                f"({len(chunks)} chunks, {len(transcript)} chars total)"
            )
            return self._summarize_with_chunking(
                chunks=chunks,
                title=title,
                channel=channel,
                duration_str=duration_str,
            )

    def _summarize_with_chunking(
        self,
        chunks: list[str],
        title: str,
        channel: str,
        duration_str: str,
    ) -> VideoSummary:
        """
        Summarize a video that requires chunking.

        Args:
            chunks: List of transcript chunks
            title: Video title
            channel: Channel name
            duration_str: Formatted duration string

        Returns:
            Synthesized VideoSummary
        """
        chunk_summaries = []

        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i + 1}/{len(chunks)}")

            prompt = SUMMARIZATION_PROMPT.format(
                title=f"{title} (Teil {i + 1}/{len(chunks)})",
                channel=channel,
                duration=duration_str,
                transcript=chunk,
            )

            summary = self._call_gemini_with_retry(prompt, VideoSummary)
            chunk_summaries.append(summary)

        # Synthesize all chunk summaries
        return self._synthesize_summaries(
            chunk_summaries=chunk_summaries,
            title=title,
            channel=channel,
            duration_str=duration_str,
        )

    def _synthesize_summaries(
        self,
        chunk_summaries: list[VideoSummary],
        title: str,
        channel: str,
        duration_str: str,
    ) -> VideoSummary:
        """
        Synthesize multiple chunk summaries into one.

        Args:
            chunk_summaries: List of summaries from chunks
            title: Video title
            channel: Channel name
            duration_str: Formatted duration string

        Returns:
            Single synthesized VideoSummary
        """
        # Format chunk summaries for the synthesis prompt
        summaries_text = ""
        for i, summary in enumerate(chunk_summaries):
            summaries_text += f"\n### Teil {i + 1}\n"
            summaries_text += f"**Kernaussage:** {summary.core_message}\n\n"
            summaries_text += f"**Zusammenfassung:** {summary.detailed_summary}\n\n"
            summaries_text += "**Key Takeaways:**\n"
            for takeaway in summary.key_takeaways:
                summaries_text += f"- {takeaway}\n"
            summaries_text += "\n"

        prompt = SYNTHESIS_PROMPT.format(
            title=title,
            channel=channel,
            duration=duration_str,
            chunk_summaries=summaries_text,
        )

        logger.info(f"Synthesizing {len(chunk_summaries)} chunk summaries")
        return self._call_gemini_with_retry(prompt, VideoSummary)

    def categorize_video(
        self,
        title: str,
        channel: str,
        description: str = "",
    ) -> Category:
        """
        Quickly categorize a video without full transcript.

        Useful for batch pre-processing or when transcript is unavailable.

        Args:
            title: Video title
            channel: Channel name
            description: Video description (optional)

        Returns:
            Category enum value
        """
        # Simple schema for category-only response
        class CategoryResponse(BaseModel):
            category: Category

        prompt = CATEGORIZATION_PROMPT.format(
            title=title,
            channel=channel,
            description=description[:1000] if description else "(keine Beschreibung)",
        )

        logger.info(f"Categorizing video: {title}")
        result = self._call_gemini_with_retry(prompt, CategoryResponse)
        return result.category

    def batch_summarize(
        self,
        videos: list[dict],
        on_progress: Optional[callable] = None,
    ) -> list[tuple[dict, Optional[VideoSummary], Optional[str]]]:
        """
        Summarize multiple videos in batch.

        Args:
            videos: List of video dicts with keys:
                    - transcript: str
                    - title: str
                    - channel: str
                    - duration_seconds: int
            on_progress: Optional callback(current, total, video_title)

        Returns:
            List of tuples: (video_dict, VideoSummary or None, error_message or None)
        """
        results = []
        total = len(videos)

        for i, video in enumerate(videos):
            if on_progress:
                on_progress(i + 1, total, video.get("title", "Unknown"))

            try:
                summary = self.summarize_video(
                    transcript=video["transcript"],
                    title=video["title"],
                    channel=video["channel"],
                    duration_seconds=video["duration_seconds"],
                )
                results.append((video, summary, None))

            except SummarizationError as e:
                logger.error(f"Failed to summarize '{video.get('title')}': {e.message}")
                results.append((video, None, e.message))

            except Exception as e:
                logger.error(f"Unexpected error for '{video.get('title')}': {e}")
                results.append((video, None, str(e)))

        return results


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse

    from app.services.transcript_service import TranscriptService

    parser = argparse.ArgumentParser(description="Summarization Service CLI")
    parser.add_argument(
        "video_id",
        help="YouTube video ID to summarize",
    )
    parser.add_argument(
        "--title",
        default="Test Video",
        help="Video title (default: Test Video)",
    )
    parser.add_argument(
        "--channel",
        default="Test Channel",
        help="Channel name (default: Test Channel)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Video duration in seconds (default: 600)",
    )
    parser.add_argument(
        "--categorize-only",
        action="store_true",
        help="Only categorize, don't summarize",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    service = SummarizationService()

    if args.categorize_only:
        category = service.categorize_video(
            title=args.title,
            channel=args.channel,
        )
        print(f"\nCategory: {category.value}")
    else:
        # Get transcript first
        print(f"Fetching transcript for {args.video_id}...")
        with TranscriptService() as ts:
            transcript_result = ts.get_transcript(args.video_id)

        print(f"Got {transcript_result.word_count} words from {transcript_result.source}")
        print("Summarizing...")

        try:
            summary = service.summarize_video(
                transcript=transcript_result.text,
                title=args.title,
                channel=args.channel,
                duration_seconds=args.duration,
            )

            print(f"\n{'='*60}")
            print(f"Category: {summary.category.value}")
            print(f"{'='*60}")
            print(f"\n## Kernaussage\n{summary.core_message}")
            print(f"\n## Zusammenfassung\n{summary.detailed_summary}")
            print("\n## Key Takeaways")
            for takeaway in summary.key_takeaways:
                print(f"  - {takeaway}")

            if summary.timestamps:
                print("\n## Timestamps")
                for ts in summary.timestamps:
                    print(f"  [{ts.time}] {ts.description}")

            if summary.action_items:
                print("\n## Action Items")
                for item in summary.action_items:
                    print(f"  - {item}")

        except SummarizationError as e:
            print(f"\nError: {e.message}")
            if e.retry_later:
                print("(Will retry later)")
