"""
Pydantic Settings Configuration for YouTube Digest
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql://youtube_digest:password@localhost:5432/youtube_digest",
        description="PostgreSQL connection string",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string for Celery",
    )

    # YouTube OAuth
    youtube_oauth_credentials_path: Path = Field(
        default=Path("/app/credentials/youtube_oauth.json"),
        description="Path to YouTube OAuth client credentials",
    )
    youtube_token_path: Path = Field(
        default=Path("/app/credentials/youtube_token.json"),
        description="Path to stored YouTube OAuth token",
    )

    # Gemini API
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model to use for summarization",
    )

    # Supadata API (Transcript Fallback)
    supadata_api_key: str = Field(
        default="",
        description="Supadata API key for transcript fallback",
    )

    # Email Configuration (Resend API)
    resend_api_key: str = Field(
        default="",
        description="Resend API key for email sending",
    )
    email_from_address: str = Field(
        default="YouTube Digest <digest@resend.dev>",
        description="Sender email (must be verified domain in Resend)",
    )
    email_to_address: str = Field(
        default="niko.huebner@gmail.com",
        description="Recipient email for digests",
    )

    # Digest Settings
    digest_interval_days: int = Field(
        default=14,
        description="Days between automatic digest emails",
    )
    digest_video_threshold: int = Field(
        default=10,
        description="Number of videos that triggers immediate digest",
    )

    # Priority categories (get more detailed summaries)
    priority_categories: List[str] = Field(
        default=["Claude Code", "Coding/AI Allgemein"],
        description="Categories that receive higher priority/detail",
    )

    # All available categories
    categories: List[str] = Field(
        default=[
            "Claude Code",
            "Coding/AI Allgemein",
            "Brettspiele",
            "Gesundheit",
            "Sport",
            "Beziehung/Sexualität",
            "Beachvolleyball",
            "Sonstige",
        ],
        description="Available video categories",
    )

    # Application
    app_env: str = Field(default="production")
    app_debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Dashboard Auth (optional)
    dashboard_username: str = Field(default="admin")
    dashboard_password: str = Field(default="")

    @field_validator("youtube_oauth_credentials_path", "youtube_token_path", mode="before")
    @classmethod
    def parse_path(cls, v):
        if isinstance(v, str):
            return Path(v)
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience access
settings = get_settings()
