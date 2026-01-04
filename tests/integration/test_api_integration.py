# tests/integration/test_api_integration.py
"""Integration tests for API endpoints with database.

Note: These tests require PostgreSQL due to JSONB column types.
They are designed to run in CI/CD with a PostgreSQL test database.
For local testing without PostgreSQL, use the unit tests in tests/unit/.

To run locally with PostgreSQL:
    DATABASE_URL=postgresql://user:pass@localhost/test_db pytest tests/integration/ -v
"""
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def _is_postgresql_available():
    """Check if PostgreSQL is configured and available."""
    db_url = os.environ.get("DATABASE_URL", "")
    if "postgresql" not in db_url:
        return False
    try:
        import psycopg2
        return True
    except ImportError:
        return False


# Skip all tests in this module if PostgreSQL is not available
if not _is_postgresql_available():
    pytest.skip(
        "Integration tests require PostgreSQL (set DATABASE_URL and install psycopg2)",
        allow_module_level=True
    )


@pytest.fixture(scope="function")
def db():
    """Create test database session for each test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    database_url = os.environ.get("DATABASE_URL")
    engine = create_engine(database_url)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create all tables
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        # Clean up all tables after test
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    """Create test client with test database."""
    with patch("app.main.init_db"):  # Skip DB initialization
        from app.main import app
        from app.models import get_db

        def override_get_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        from fastapi.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()


class TestHealthEndpoint:
    """Integration tests for health endpoint."""

    @patch("app.api.routes.celery_app")
    def test_health_check_database(self, mock_celery, client, db):
        """Health check should verify database connection."""
        mock_celery.control.ping.return_value = [{"worker1": {"ok": "pong"}}]
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["database"] is True


class TestChannelsEndpoint:
    """Integration tests for channels endpoint."""

    def test_list_channels_empty(self, client, db):
        """Should return empty list when no channels."""
        response = client.get("/api/channels")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["channels"] == []

    def test_list_channels_with_data(self, client, db):
        """Should return channels from database."""
        from app.models import Channel
        # Add test channel
        channel = Channel(
            channel_id="UC123",
            channel_name="Test Channel",
            is_active=True,
        )
        db.add(channel)
        db.commit()

        response = client.get("/api/channels")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["channels"][0]["channel_id"] == "UC123"


class TestVideosEndpoint:
    """Integration tests for videos endpoint."""

    def test_list_videos_empty(self, client, db):
        """Should return empty list when no videos."""
        response = client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_list_videos_with_filters(self, client, db):
        """Should filter videos by category and status."""
        from app.models import Channel, ProcessedVideo
        # Add test channel and video
        channel = Channel(
            channel_id="UC123",
            channel_name="Test Channel",
        )
        db.add(channel)
        db.flush()

        video = ProcessedVideo(
            video_id="vid123",
            channel_id="UC123",
            title="Test Video",
            category="Claude Code",
            processing_status="completed",
        )
        db.add(video)
        db.commit()

        # Filter by category
        response = client.get("/api/videos?category=Claude%20Code")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        # Filter by different category
        response = client.get("/api/videos?category=Sonstige")
        data = response.json()
        assert data["total"] == 0

    def test_get_video_detail(self, client, db):
        """Should return video details."""
        from app.models import Channel, ProcessedVideo
        channel = Channel(
            channel_id="UC123",
            channel_name="Test Channel",
        )
        db.add(channel)
        db.flush()

        video = ProcessedVideo(
            video_id="vid123",
            channel_id="UC123",
            title="Test Video",
            summary={
                "core_message": "Test message",
                "detailed_summary": "Test summary",
                "key_takeaways": ["Point 1"],
                "timestamps": [],
                "action_items": [],
            },
            processing_status="completed",
        )
        db.add(video)
        db.commit()

        response = client.get("/api/videos/vid123")
        assert response.status_code == 200
        data = response.json()
        assert data["video_id"] == "vid123"
        assert data["summary"]["core_message"] == "Test message"

    def test_get_video_not_found(self, client, db):
        """Should return 404 for missing video."""
        response = client.get("/api/videos/nonexistent")
        assert response.status_code == 404


class TestDigestsEndpoint:
    """Integration tests for digests endpoint."""

    def test_list_digests_empty(self, client, db):
        """Should return empty list when no digests."""
        response = client.get("/api/digests")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_list_digests_with_data(self, client, db):
        """Should return digest history."""
        from app.models import DigestHistory
        digest = DigestHistory(
            sent_at=datetime.now(timezone.utc),
            video_count=5,
            email_status="sent",
            trigger_reason="scheduled",
        )
        db.add(digest)
        db.commit()

        response = client.get("/api/digests")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["digests"][0]["video_count"] == 5


class TestStatusEndpoint:
    """Integration tests for status endpoint."""

    @patch("app.api.routes.celery_app")
    def test_status_returns_counts(self, mock_celery, client, db):
        """Should return video counts by status."""
        from app.models import Channel, ProcessedVideo
        # Mock Celery inspect to avoid Redis connection
        mock_celery.control.inspect.return_value.active.return_value = None

        # Add videos with different statuses
        channel = Channel(channel_id="UC123", channel_name="Test")
        db.add(channel)
        db.flush()

        for i, status in enumerate(["pending", "processing", "completed", "failed"]):
            video = ProcessedVideo(
                video_id=f"vid{i}",
                channel_id="UC123",
                title=f"Video {i}",
                processing_status=status,
            )
            db.add(video)
        db.commit()

        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["pending_videos"] == 1
        assert data["processing_videos"] == 1
        assert data["completed_videos"] == 1
        assert data["failed_videos"] == 1
