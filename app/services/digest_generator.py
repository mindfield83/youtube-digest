"""
Digest Generator Service for YouTube Digest

Handles:
- Grouping videos by category
- Sorting categories by priority
- Generating HTML digest from Jinja2 template
- Creating plain-text fallback
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.models import ProcessedVideo

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# Category priority order (lower = higher priority)
CATEGORY_PRIORITY = {
    "Claude Code": 0,
    "Coding/AI Allgemein": 1,
    "Brettspiele": 2,
    "Gesundheit": 3,
    "Sport": 4,
    "Beziehung/Sexualität": 5,
    "Beachvolleyball": 6,
    "Sonstige": 99,
}

# Maximum videos per digest to prevent huge emails
MAX_VIDEOS_PER_DIGEST = 50

# Dashboard base URL
DASHBOARD_BASE_URL = "https://youtube-digest.vps-ubuntu.mindfield.de"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class VideoDigestItem:
    """Prepared video data for template rendering."""

    video_id: str
    title: str
    channel_name: str
    duration: str
    published_at: datetime
    category: str
    core_message: str
    key_takeaways: list[str]
    action_items: list[str]
    youtube_url: str
    summary_url: str


@dataclass
class DigestResult:
    """Result of digest generation."""

    html: str
    plain_text: str
    subject: str
    video_count: int
    total_duration_seconds: int
    category_counts: dict[str, int]
    period_start: datetime
    period_end: datetime
    videos: list[VideoDigestItem] = field(default_factory=list)


# =============================================================================
# Exceptions
# =============================================================================


class DigestGenerationError(Exception):
    """Error during digest generation."""

    pass


# =============================================================================
# Service Class
# =============================================================================


class DigestGenerator:
    """Service for generating HTML digest emails from processed videos."""

    def __init__(self, template_dir: Optional[Path] = None):
        """
        Initialize the digest generator.

        Args:
            template_dir: Path to Jinja2 templates (uses default if not provided)
        """
        self.template_dir = template_dir or TEMPLATE_DIR
        self._env: Optional[Environment] = None

    @property
    def env(self) -> Environment:
        """Get or create Jinja2 environment."""
        if self._env is None:
            self._env = Environment(
                loader=FileSystemLoader(self.template_dir),
                autoescape=select_autoescape(["html", "xml"]),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            # Add custom filters
            self._env.filters["format_date"] = self._format_date
            self._env.filters["format_datetime"] = self._format_datetime
        return self._env

    @staticmethod
    def _format_date(dt: datetime) -> str:
        """Format datetime as German date string."""
        return dt.strftime("%d.%m.%Y")

    @staticmethod
    def _format_datetime(dt: datetime) -> str:
        """Format datetime as German datetime string."""
        return dt.strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format duration in seconds to human-readable string."""
        hours, remainder = divmod(seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}min"
        return f"{minutes}min"

    def _prepare_video_item(self, video: ProcessedVideo) -> Optional[VideoDigestItem]:
        """
        Convert ProcessedVideo to VideoDigestItem for template.

        Args:
            video: ProcessedVideo from database

        Returns:
            VideoDigestItem or None if video has no summary
        """
        # Skip videos without summary
        if not video.summary:
            logger.warning(f"Video {video.video_id} has no summary, skipping")
            return None

        summary = video.summary

        # Extract fields from summary JSON
        core_message = summary.get("core_message", "")
        key_takeaways = summary.get("key_takeaways", [])
        action_items = summary.get("action_items", [])

        if not core_message:
            logger.warning(f"Video {video.video_id} has empty core_message, skipping")
            return None

        return VideoDigestItem(
            video_id=video.video_id,
            title=video.title,
            channel_name=video.channel.channel_name if video.channel else "Unbekannt",
            duration=video.duration_formatted,
            published_at=video.published_at,
            category=video.category,
            core_message=core_message,
            key_takeaways=key_takeaways[:10],  # Limit to 10 takeaways
            action_items=action_items[:5],  # Limit to 5 action items
            youtube_url=video.youtube_url,
            summary_url=f"{DASHBOARD_BASE_URL}/video/{video.video_id}",
        )

    def _group_by_category(
        self, videos: list[VideoDigestItem]
    ) -> dict[str, list[VideoDigestItem]]:
        """
        Group videos by category, sorted by priority.

        Args:
            videos: List of prepared video items

        Returns:
            Dict with category names as keys, sorted by priority
        """
        # Group videos
        groups: dict[str, list[VideoDigestItem]] = {}
        for video in videos:
            category = video.category
            if category not in groups:
                groups[category] = []
            groups[category].append(video)

        # Sort videos within each category by published date (newest first)
        for category in groups:
            groups[category].sort(key=lambda v: v.published_at, reverse=True)

        # Sort categories by priority
        sorted_categories = sorted(
            groups.keys(),
            key=lambda c: CATEGORY_PRIORITY.get(c, 50),
        )

        return {cat: groups[cat] for cat in sorted_categories}

    def _calculate_stats(
        self, videos: list[ProcessedVideo]
    ) -> tuple[int, dict[str, int]]:
        """
        Calculate statistics for the digest.

        Args:
            videos: List of ProcessedVideo objects

        Returns:
            Tuple of (total_duration_seconds, category_counts)
        """
        total_duration = sum(v.duration_seconds for v in videos)

        category_counts: dict[str, int] = {}
        for video in videos:
            cat = video.category
            category_counts[cat] = category_counts.get(cat, 0) + 1

        return total_duration, category_counts

    def generate(
        self,
        videos: list[ProcessedVideo],
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> DigestResult:
        """
        Generate a digest from a list of processed videos.

        Args:
            videos: List of ProcessedVideo objects from database
            period_start: Start of the digest period (auto-detected if not provided)
            period_end: End of the digest period (defaults to now)

        Returns:
            DigestResult with HTML, plain text, and metadata

        Raises:
            DigestGenerationError: If generation fails
        """
        if not videos:
            raise DigestGenerationError("No videos provided for digest")

        # Limit videos
        if len(videos) > MAX_VIDEOS_PER_DIGEST:
            logger.warning(
                f"Limiting digest from {len(videos)} to {MAX_VIDEOS_PER_DIGEST} videos"
            )
            # Sort by published date and take newest
            videos = sorted(videos, key=lambda v: v.published_at, reverse=True)[
                :MAX_VIDEOS_PER_DIGEST
            ]

        # Prepare video items
        video_items = []
        for video in videos:
            item = self._prepare_video_item(video)
            if item:
                video_items.append(item)

        if not video_items:
            raise DigestGenerationError("No videos with valid summaries found")

        # Calculate period
        if period_end is None:
            period_end = datetime.now(timezone.utc)
        if period_start is None:
            # Use oldest video's published date
            period_start = min(v.published_at for v in video_items)

        # Group by category
        grouped_videos = self._group_by_category(video_items)

        # Calculate stats
        total_duration, category_counts = self._calculate_stats(videos)

        # Generate subject
        subject = (
            f"YouTube Digest: {len(video_items)} neue Videos "
            f"({self._format_date(period_start)} - {self._format_date(period_end)})"
        )

        # Render HTML
        try:
            template = self.env.get_template("digest_email.html")
            html = template.render(
                subject=subject,
                period_start=period_start,
                period_end=period_end,
                video_count=len(video_items),
                total_duration=self._format_duration(total_duration),
                grouped_videos=grouped_videos,
                category_counts=category_counts,
                dashboard_url=f"{DASHBOARD_BASE_URL}/dashboard",
                generated_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            raise DigestGenerationError(f"Failed to render template: {e}")

        # Generate plain text
        plain_text = self._generate_plain_text(
            video_items=video_items,
            grouped_videos=grouped_videos,
            period_start=period_start,
            period_end=period_end,
            total_duration=total_duration,
        )

        logger.info(
            f"Generated digest with {len(video_items)} videos "
            f"in {len(grouped_videos)} categories"
        )

        return DigestResult(
            html=html,
            plain_text=plain_text,
            subject=subject,
            video_count=len(video_items),
            total_duration_seconds=total_duration,
            category_counts=category_counts,
            period_start=period_start,
            period_end=period_end,
            videos=video_items,
        )

    def _generate_plain_text(
        self,
        video_items: list[VideoDigestItem],
        grouped_videos: dict[str, list[VideoDigestItem]],
        period_start: datetime,
        period_end: datetime,
        total_duration: int,
    ) -> str:
        """
        Generate plain text version of digest.

        Uses manual formatting for better control than html2text.
        """
        lines = [
            "=" * 60,
            "YOUTUBE DIGEST",
            f"{self._format_date(period_start)} - {self._format_date(period_end)}",
            f"{len(video_items)} Videos | {self._format_duration(total_duration)} Gesamtdauer",
            "=" * 60,
            "",
        ]

        for category, videos in grouped_videos.items():
            lines.append(f"\n{'-' * 40}")
            lines.append(f"> {category.upper()} ({len(videos)} Videos)")
            lines.append("-" * 40)

            for video in videos:
                lines.append(f"\n[VIDEO] {video.title}")
                lines.append(f"   {video.channel_name} | {video.duration}")
                lines.append(f"   {video.youtube_url}")
                lines.append("")
                lines.append(f"   {video.core_message}")
                lines.append("")

                if video.key_takeaways:
                    lines.append("   Key Takeaways:")
                    for takeaway in video.key_takeaways[:5]:
                        lines.append(f"   * {takeaway}")

                if video.action_items:
                    lines.append("\n   Action Items:")
                    for item in video.action_items[:3]:
                        lines.append(f"   -> {item}")

                lines.append(
                    f"\n   -> Vollstaendige Zusammenfassung: {video.summary_url}"
                )
                lines.append("")

        lines.extend(
            [
                "",
                "=" * 60,
                f"Dashboard: {DASHBOARD_BASE_URL}/dashboard",
                f"Generiert: {self._format_datetime(datetime.now(timezone.utc))}",
                "=" * 60,
            ]
        )

        return "\n".join(lines)


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    from sqlalchemy.orm import Session

    from app.models import SessionLocal

    parser = argparse.ArgumentParser(description="Digest Generator CLI")
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days to include in digest (default: 14)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output HTML file path",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Open in browser after generation",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # Get videos from database
    db: Session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        videos = (
            db.query(ProcessedVideo)
            .filter(ProcessedVideo.processing_status == "completed")
            .filter(ProcessedVideo.published_at >= cutoff)
            .all()
        )

        if not videos:
            print(f"No completed videos found in the last {args.days} days")
            exit(1)

        print(f"Found {len(videos)} videos")

        # Generate digest
        generator = DigestGenerator()
        result = generator.generate(videos)

        print(f"\nSubject: {result.subject}")
        print(f"Videos: {result.video_count}")
        print(f"Duration: {generator._format_duration(result.total_duration_seconds)}")
        print(f"Categories: {result.category_counts}")

        # Save or preview
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(result.html, encoding="utf-8")
            print(f"\nSaved to: {output_path}")

            if args.preview:
                import webbrowser

                webbrowser.open(output_path.as_uri())
        else:
            print("\n--- Plain Text Preview ---")
            print(result.plain_text[:2000])
            print("...")

    finally:
        db.close()
