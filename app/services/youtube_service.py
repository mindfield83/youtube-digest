"""
YouTube Service for YouTube Digest

Handles:
- OAuth 2.0 authentication with YouTube API
- Fetching user subscriptions
- Fetching new videos from subscribed channels
- Filtering out Shorts and Livestreams
"""
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import isodate
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings

logger = logging.getLogger(__name__)

# YouTube API scopes
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

# Minimum video duration to not be considered a Short (60 seconds)
MIN_VIDEO_DURATION_SECONDS = 60


class YouTubeServiceError(Exception):
    """Base exception for YouTube service errors."""
    pass


class OAuthError(YouTubeServiceError):
    """OAuth authentication error."""
    pass


class QuotaExceededError(YouTubeServiceError):
    """YouTube API quota exceeded."""
    pass


def parse_iso8601_duration(duration_str: str) -> int:
    """
    Parse ISO 8601 duration string to seconds.

    Examples:
        PT15M30S -> 930 seconds
        PT1H30M -> 5400 seconds
        PT45S -> 45 seconds
    """
    try:
        duration = isodate.parse_duration(duration_str)
        return int(duration.total_seconds())
    except Exception:
        logger.warning(f"Failed to parse duration: {duration_str}")
        return 0


def is_valid_video(video: dict) -> bool:
    """
    Check if a video should be processed.

    Filters out:
    - Shorts (< 60 seconds)
    - Livestreams (active or past)
    - Upcoming premieres

    Args:
        video: Video resource from YouTube API

    Returns:
        True if video should be processed, False otherwise
    """
    # Check for livestream
    if "liveStreamingDetails" in video:
        logger.debug(f"Filtering livestream: {video.get('snippet', {}).get('title', 'Unknown')}")
        return False

    # Check liveBroadcastContent in snippet
    snippet = video.get("snippet", {})
    live_status = snippet.get("liveBroadcastContent", "none")
    if live_status in ("live", "upcoming"):
        logger.debug(f"Filtering live/upcoming: {snippet.get('title', 'Unknown')}")
        return False

    # Check duration for Shorts
    content_details = video.get("contentDetails", {})
    duration_str = content_details.get("duration", "PT0S")
    duration_seconds = parse_iso8601_duration(duration_str)

    if duration_seconds < MIN_VIDEO_DURATION_SECONDS:
        logger.debug(
            f"Filtering Short ({duration_seconds}s): {snippet.get('title', 'Unknown')}"
        )
        return False

    return True


class YouTubeService:
    """Service for interacting with YouTube Data API v3."""

    def __init__(
        self,
        credentials_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
    ):
        """
        Initialize YouTube service.

        Args:
            credentials_path: Path to OAuth client credentials JSON
            token_path: Path to store/load OAuth tokens
        """
        self.credentials_path = credentials_path or settings.youtube_oauth_credentials_path
        self.token_path = token_path or settings.youtube_token_path
        self._credentials: Optional[Credentials] = None
        self._youtube: Any = None

    @property
    def credentials(self) -> Credentials:
        """Get or load OAuth credentials."""
        if self._credentials is None:
            self._credentials = self._load_credentials()
        return self._credentials

    @property
    def youtube(self) -> Any:
        """Get YouTube API client."""
        if self._youtube is None:
            self._youtube = build("youtube", "v3", credentials=self.credentials)
        return self._youtube

    def _load_credentials(self) -> Credentials:
        """
        Load OAuth credentials from file or initiate OAuth flow.

        Returns:
            Valid OAuth credentials

        Raises:
            OAuthError: If credentials cannot be loaded or refreshed
        """
        creds = None

        # Try to load existing token
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_path), SCOPES
                )
                logger.info("Loaded existing OAuth token")
            except Exception as e:
                logger.warning(f"Failed to load token: {e}")

        # Check if credentials are valid or need refresh
        if creds:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self._save_credentials(creds)
                    logger.info("Refreshed OAuth token")
                except Exception as e:
                    logger.error(f"Failed to refresh token: {e}")
                    creds = None
            elif not creds.valid:
                creds = None

        # If no valid credentials, need to run OAuth flow
        if not creds:
            if not self.credentials_path.exists():
                raise OAuthError(
                    f"OAuth credentials not found at {self.credentials_path}. "
                    "Run OAuth flow first with --auth flag."
                )
            raise OAuthError(
                "No valid OAuth token. Run OAuth flow first with --auth flag."
            )

        return creds

    def _save_credentials(self, creds: Credentials) -> None:
        """Save credentials to token file."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_path, "w") as f:
            f.write(creds.to_json())
        logger.info(f"Saved OAuth token to {self.token_path}")

    def run_oauth_flow(self, port: int = 8080) -> Credentials:
        """
        Run OAuth 2.0 flow to get new credentials.

        This should be run locally/interactively to authorize the application.

        Args:
            port: Port for local OAuth callback server

        Returns:
            New OAuth credentials
        """
        if not self.credentials_path.exists():
            raise OAuthError(
                f"OAuth credentials file not found at {self.credentials_path}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.credentials_path), SCOPES
        )
        creds = flow.run_local_server(port=port)
        self._save_credentials(creds)
        self._credentials = creds
        self._youtube = None  # Reset client
        logger.info("OAuth flow completed successfully")
        return creds

    def get_subscriptions(self, max_results: int = 50) -> list[dict]:
        """
        Get all subscribed channels for the authenticated user.

        Args:
            max_results: Maximum results per page (max 50)

        Returns:
            List of channel dictionaries with id, name, thumbnail
        """
        channels = []
        page_token = None

        try:
            while True:
                request = self.youtube.subscriptions().list(
                    part="snippet",
                    mine=True,
                    maxResults=min(max_results, 50),
                    pageToken=page_token,
                )
                response = request.execute()

                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    resource_id = snippet.get("resourceId", {})
                    channels.append({
                        "channel_id": resource_id.get("channelId"),
                        "channel_name": snippet.get("title"),
                        "channel_url": f"https://www.youtube.com/channel/{resource_id.get('channelId')}",
                        "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                    })

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info(f"Retrieved {len(channels)} subscribed channels")
            return channels

        except HttpError as e:
            if e.resp.status == 403 and "quotaExceeded" in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise YouTubeServiceError(f"Failed to get subscriptions: {e}")

    def get_channel_upload_playlist_id(self, channel_id: str) -> Optional[str]:
        """
        Get the uploads playlist ID for a channel.

        The uploads playlist has the same ID as the channel but with 'UU' prefix
        instead of 'UC'.

        Args:
            channel_id: YouTube channel ID

        Returns:
            Uploads playlist ID or None if channel not found
        """
        # Shortcut: UC -> UU transformation
        if channel_id.startswith("UC"):
            return "UU" + channel_id[2:]

        # Otherwise, query the API
        try:
            request = self.youtube.channels().list(
                part="contentDetails",
                id=channel_id,
            )
            response = request.execute()

            items = response.get("items", [])
            if items:
                return items[0].get("contentDetails", {}).get(
                    "relatedPlaylists", {}
                ).get("uploads")
            return None

        except HttpError as e:
            logger.error(f"Failed to get upload playlist for {channel_id}: {e}")
            return None

    def get_channel_videos(
        self,
        channel_id: str,
        since_date: Optional[datetime] = None,
        max_results: int = 50,
    ) -> list[dict]:
        """
        Get videos from a channel's uploads playlist.

        Args:
            channel_id: YouTube channel ID
            since_date: Only return videos published after this date
            max_results: Maximum number of videos to return

        Returns:
            List of video dictionaries (not yet filtered for Shorts/Livestreams)
        """
        playlist_id = self.get_channel_upload_playlist_id(channel_id)
        if not playlist_id:
            logger.warning(f"Could not find uploads playlist for channel {channel_id}")
            return []

        videos = []
        page_token = None

        try:
            while len(videos) < max_results:
                request = self.youtube.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=min(50, max_results - len(videos)),
                    pageToken=page_token,
                )
                response = request.execute()

                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    content_details = item.get("contentDetails", {})

                    # Parse publish date
                    published_str = snippet.get("publishedAt", "")
                    try:
                        published_at = datetime.fromisoformat(
                            published_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        published_at = datetime.utcnow()

                    # Check if video is after since_date
                    if since_date and published_at.replace(tzinfo=None) < since_date:
                        # Videos are ordered newest first, so we can stop
                        logger.debug(f"Reached videos older than {since_date}")
                        return videos

                    videos.append({
                        "video_id": content_details.get("videoId"),
                        "title": snippet.get("title"),
                        "description": snippet.get("description", ""),
                        "published_at": published_at,
                        "channel_id": channel_id,
                        "channel_name": snippet.get("videoOwnerChannelTitle", ""),
                        "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                    })

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info(f"Retrieved {len(videos)} videos from channel {channel_id}")
            return videos

        except HttpError as e:
            if e.resp.status == 403 and "quotaExceeded" in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            logger.error(f"Failed to get videos for channel {channel_id}: {e}")
            return []

    def get_video_details(self, video_ids: list[str]) -> list[dict]:
        """
        Get detailed information for videos including duration and live status.

        Args:
            video_ids: List of video IDs (max 50 per call)

        Returns:
            List of video detail dictionaries
        """
        if not video_ids:
            return []

        # API allows max 50 IDs per request
        all_videos = []

        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]

            try:
                request = self.youtube.videos().list(
                    part="snippet,contentDetails,liveStreamingDetails",
                    id=",".join(batch),
                )
                response = request.execute()

                for item in response.get("items", []):
                    snippet = item.get("snippet", {})
                    content_details = item.get("contentDetails", {})

                    # Parse duration
                    duration_str = content_details.get("duration", "PT0S")
                    duration_seconds = parse_iso8601_duration(duration_str)

                    video_data = {
                        "video_id": item.get("id"),
                        "title": snippet.get("title"),
                        "description": snippet.get("description", ""),
                        "duration_seconds": duration_seconds,
                        "duration_str": duration_str,
                        "published_at": snippet.get("publishedAt"),
                        "channel_id": snippet.get("channelId"),
                        "channel_name": snippet.get("channelTitle"),
                        "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                        "live_broadcast_content": snippet.get("liveBroadcastContent", "none"),
                    }

                    # Add livestream details if present
                    if "liveStreamingDetails" in item:
                        video_data["liveStreamingDetails"] = item["liveStreamingDetails"]

                    # Add full item for is_valid_video check
                    video_data["_raw"] = item

                    all_videos.append(video_data)

            except HttpError as e:
                if e.resp.status == 403 and "quotaExceeded" in str(e):
                    raise QuotaExceededError("YouTube API quota exceeded")
                logger.error(f"Failed to get video details: {e}")

        return all_videos

    def get_new_videos_from_subscriptions(
        self,
        since_date: Optional[datetime] = None,
        channel_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Get new videos from all subscribed channels (or specified channels).

        This is the main method for fetching new content. It:
        1. Gets all subscriptions (or uses provided channel_ids)
        2. Fetches recent videos from each channel
        3. Gets full details for filtering
        4. Filters out Shorts and Livestreams

        Args:
            since_date: Only return videos published after this date
            channel_ids: Optional list of specific channel IDs to check

        Returns:
            List of valid video dictionaries ready for processing
        """
        if since_date is None:
            since_date = datetime.utcnow() - timedelta(days=14)

        # Get channels to check
        if channel_ids:
            channels = [{"channel_id": cid} for cid in channel_ids]
        else:
            channels = self.get_subscriptions()

        # Collect video IDs from all channels
        all_video_ids = []
        video_metadata = {}  # video_id -> basic metadata

        for channel in channels:
            channel_id = channel.get("channel_id")
            if not channel_id:
                continue

            videos = self.get_channel_videos(
                channel_id,
                since_date=since_date,
                max_results=20,  # Limit per channel to manage quota
            )

            for video in videos:
                video_id = video.get("video_id")
                if video_id:
                    all_video_ids.append(video_id)
                    video_metadata[video_id] = video

        logger.info(f"Found {len(all_video_ids)} videos from {len(channels)} channels")

        if not all_video_ids:
            return []

        # Get full details for filtering
        detailed_videos = self.get_video_details(all_video_ids)

        # Filter and combine data
        valid_videos = []
        for video in detailed_videos:
            raw_video = video.get("_raw", video)

            if not is_valid_video(raw_video):
                continue

            # Merge with metadata from playlist query
            basic = video_metadata.get(video.get("video_id"), {})
            video.update({
                "published_at": basic.get("published_at") or video.get("published_at"),
            })

            # Remove internal fields
            video.pop("_raw", None)

            valid_videos.append(video)

        logger.info(f"After filtering: {len(valid_videos)} valid videos")
        return valid_videos


# CLI for OAuth flow
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="YouTube Service CLI")
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Run OAuth flow to get new credentials",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test by fetching subscriptions",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for OAuth callback server",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    service = YouTubeService()

    if args.auth:
        print("Starting OAuth flow...")
        print(f"OAuth callback will listen on port {args.port}")
        creds = service.run_oauth_flow(port=args.port)
        print(f"Success! Token saved to {service.token_path}")

    if args.test or not args.auth:
        try:
            print("\nFetching subscriptions...")
            channels = service.get_subscriptions()
            print(f"\nFound {len(channels)} subscribed channels:")
            for ch in channels[:10]:
                print(f"  - {ch['channel_name']}")
            if len(channels) > 10:
                print(f"  ... and {len(channels) - 10} more")
        except OAuthError as e:
            print(f"\nOAuth Error: {e}")
            print("Run with --auth to authorize the application")
