# tests/unit/test_api_routes.py
"""Unit tests for API routes."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_db():
    """Create mock database session."""
    db = MagicMock()
    # Setup default returns for common queries
    db.query.return_value.filter.return_value.count.return_value = 0
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.order_by.return_value.first.return_value = None
    db.query.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.all.return_value = []
    db.query.return_value.count.return_value = 0
    db.execute.return_value = None
    return db


@pytest.fixture
def client(mock_db):
    """Create test client with mocked database."""
    # Import and patch before creating app
    with patch("app.main.init_db"):  # Skip DB initialization
        from app.main import app
        from app.models import get_db

        # Override the database dependency
        def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db

        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client

        # Clean up
        app.dependency_overrides.clear()


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get("/health")
        assert response.status_code == 200

    @patch("app.api.routes.celery_app")
    def test_health_returns_status(self, mock_celery, client):
        """Health endpoint should return status."""
        mock_celery.control.ping.return_value = [{"worker1": {"ok": "pong"}}]
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"


class TestStatusEndpoint:
    """Tests for /api/status endpoint."""

    def test_status_returns_200(self, client, mock_db):
        """Status endpoint should return 200."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.first.return_value = None

        response = client.get("/api/status")
        assert response.status_code == 200

    def test_status_returns_counts(self, client, mock_db):
        """Status should include video counts."""
        mock_db.query.return_value.filter.return_value.count.return_value = 5
        mock_db.query.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value.count.return_value = 10

        response = client.get("/api/status")
        data = response.json()
        assert "pending_videos" in data


class TestChannelsEndpoint:
    """Tests for /api/channels endpoint."""

    def test_channels_returns_200(self, client, mock_db):
        """Channels endpoint should return 200."""
        mock_db.query.return_value.all.return_value = []

        response = client.get("/api/channels")
        assert response.status_code == 200

    def test_channels_returns_list(self, client, mock_db):
        """Channels should return list structure."""
        mock_db.query.return_value.all.return_value = []

        response = client.get("/api/channels")
        data = response.json()
        assert "channels" in data
        assert "total" in data


class TestVideosEndpoint:
    """Tests for /api/videos endpoint."""

    def test_videos_returns_200(self, client, mock_db):
        """Videos endpoint should return 200."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        response = client.get("/api/videos")
        assert response.status_code == 200


class TestDigestsEndpoint:
    """Tests for /api/digests endpoint."""

    def test_digests_returns_200(self, client, mock_db):
        """Digests endpoint should return 200."""
        mock_db.query.return_value.order_by.return_value.all.return_value = []

        response = client.get("/api/digests")
        assert response.status_code == 200


class TestTriggerDigestEndpoint:
    """Tests for POST /api/trigger-digest endpoint."""

    @patch("app.api.routes.generate_and_send_digest")
    def test_trigger_digest_queues_task(self, mock_task, client, mock_db):
        """Trigger should queue digest task."""
        # Setup: there are videos to process
        mock_db.query.return_value.filter.return_value.count.return_value = 5
        mock_task.delay.return_value = MagicMock(id="task-123")

        response = client.post("/api/trigger-digest")
        assert response.status_code == 200
        mock_task.delay.assert_called_once()

    @patch("app.api.routes.generate_and_send_digest")
    def test_trigger_returns_task_id(self, mock_task, client, mock_db):
        """Trigger should return task ID."""
        # Setup: there are videos to process
        mock_db.query.return_value.filter.return_value.count.return_value = 5
        mock_task.delay.return_value = MagicMock(id="task-123")

        response = client.post("/api/trigger-digest")
        data = response.json()
        assert data["task_id"] == "task-123"
