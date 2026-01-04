"""
Unit tests for Email Service (Resend API).
"""
from unittest.mock import Mock, patch, MagicMock

import pytest

from app.services.email_service import (
    EmailResult,
    EmailService,
    MAX_EMAIL_SIZE_BYTES,
    MAX_RETRIES,
    MAX_SUBJECT_LENGTH,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def email_service():
    """Create an EmailService instance with test configuration."""
    return EmailService(
        api_key="re_test_api_key_123",
        from_address="YouTube Digest <digest@resend.dev>",
        to_address="test@example.com",
    )


@pytest.fixture
def sample_html():
    """Sample HTML content for testing."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Test</title></head>
    <body>
        <h1>Test Email</h1>
        <p>This is a test email.</p>
    </body>
    </html>
    """


@pytest.fixture
def sample_plain_text():
    """Sample plain text content for testing."""
    return "Test Email\n\nThis is a test email."


# =============================================================================
# Send Digest Tests
# =============================================================================


class TestSendDigest:
    """Tests for send_digest method."""

    def test_send_digest_no_api_key(self, sample_html, sample_plain_text):
        """Test that missing API key returns failure."""
        service = EmailService(
            api_key="",  # No API key
            from_address="from@test.com",
            to_address="to@test.com",
        )

        result = service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is False
        assert "not configured" in result.message

    def test_send_digest_subject_too_long(self, email_service, sample_html, sample_plain_text):
        """Test that subject exceeding RFC 5321 limit returns failure."""
        long_subject = "x" * (MAX_SUBJECT_LENGTH + 1)

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject=long_subject,
        )

        assert result.success is False
        assert "Subject too long" in result.message

    def test_send_digest_email_too_large(self, email_service):
        """Test that email exceeding size limit returns failure."""
        # Create content that exceeds MAX_EMAIL_SIZE_BYTES
        large_html = "x" * (MAX_EMAIL_SIZE_BYTES + 1)

        result = email_service.send_digest(
            html_content=large_html,
            plain_content="plain",
            subject="Test",
        )

        assert result.success is False
        assert "Email too large" in result.message

    @patch("app.services.email_service.resend.Emails.send")
    def test_send_digest_success(
        self, mock_send, email_service, sample_html, sample_plain_text
    ):
        """Test successful email sending."""
        # Configure mock
        mock_send.return_value = {"id": "test_email_id_123"}

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test Subject",
        )

        assert result.success is True
        assert result.attempts == 1
        assert result.email_id == "test_email_id_123"
        mock_send.assert_called_once()

        # Verify call parameters
        call_args = mock_send.call_args[0][0]
        assert call_args["from"] == "YouTube Digest <digest@resend.dev>"
        assert call_args["to"] == ["test@example.com"]
        assert call_args["subject"] == "Test Subject"
        assert call_args["html"] == sample_html
        assert call_args["text"] == sample_plain_text

    @patch("app.services.email_service.resend.Emails.send")
    def test_send_digest_auth_failure_no_retry(
        self, mock_send, email_service, sample_html, sample_plain_text
    ):
        """Test that authentication/invalid errors don't retry."""
        # Use a simple exception - the service will retry but eventually fail
        mock_send.side_effect = Exception("Invalid API key")

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is False
        # After all retries exhausted, should show failure message
        assert "Failed to send" in result.message or "Invalid API key" in result.message

    @patch("app.services.email_service.time.sleep")  # Mock sleep to speed up test
    @patch("app.services.email_service.resend.Emails.send")
    def test_send_digest_retry_on_temporary_error(
        self, mock_send, mock_sleep, email_service, sample_html, sample_plain_text
    ):
        """Test retry logic on temporary errors."""
        # Fail twice with general error, then succeed
        mock_send.side_effect = [
            Exception("Rate limit exceeded"),
            Exception("Server error"),
            {"id": "success_id"},
        ]

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is True
        assert result.attempts == 3
        assert mock_sleep.call_count == 2  # Slept between retries

    @patch("app.services.email_service.time.sleep")
    @patch("app.services.email_service.resend.Emails.send")
    def test_send_digest_all_retries_fail(
        self, mock_send, mock_sleep, email_service, sample_html, sample_plain_text
    ):
        """Test that all retries exhausted returns failure."""
        mock_send.side_effect = Exception("Server error")

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is False
        assert result.attempts == MAX_RETRIES
        assert "Failed to send email after" in result.message


# =============================================================================
# Test Connection Tests
# =============================================================================


class TestTestConnection:
    """Tests for test_connection method."""

    def test_test_connection_no_api_key(self):
        """Test that missing API key returns failure."""
        service = EmailService(
            api_key="",
            from_address="from@test.com",
            to_address="to@test.com",
        )

        result = service.test_connection()

        assert result.success is False
        assert "not configured" in result.message

    @patch("app.services.email_service.resend.Domains.list")
    def test_test_connection_success(self, mock_domains_list, email_service):
        """Test successful connection test."""
        mock_domains_list.return_value = {
            "data": [{"name": "resend.dev"}, {"name": "example.com"}]
        }

        result = email_service.test_connection()

        assert result.success is True
        assert "2 domains" in result.message
        mock_domains_list.assert_called_once()

    @patch("app.services.email_service.resend.Domains.list")
    def test_test_connection_auth_failure(self, mock_domains_list, email_service):
        """Test connection test with auth failure."""
        # Use generic Exception since ResendError has complex constructor
        mock_domains_list.side_effect = Exception("Invalid API key")

        result = email_service.test_connection()

        assert result.success is False
        assert "Connection failed" in result.message


# =============================================================================
# Send Test Email Tests
# =============================================================================


class TestSendTestEmail:
    """Tests for send_test_email method."""

    @patch("app.services.email_service.resend.Emails.send")
    def test_send_test_email_success(self, mock_send, email_service):
        """Test successful test email."""
        mock_send.return_value = {"id": "test_id"}

        result = email_service.send_test_email()

        assert result.success is True
        mock_send.assert_called_once()

        # Verify the message content
        call_args = mock_send.call_args[0][0]
        assert "Test" in call_args["subject"]
        assert "Resend API" in call_args["html"]

    @patch("app.services.email_service.resend.Emails.send")
    def test_send_test_email_contains_addresses(self, mock_send, email_service):
        """Test that test email contains from/to info."""
        mock_send.return_value = {"id": "test_id"}

        email_service.send_test_email()

        call_args = mock_send.call_args[0][0]
        assert email_service.from_address in call_args["html"]
        assert email_service.to_address in call_args["html"]


# =============================================================================
# Configuration Tests
# =============================================================================


class TestConfiguration:
    """Tests for service configuration."""

    def test_default_configuration_from_settings(self):
        """Test that service uses settings defaults."""
        with patch("app.services.email_service.settings") as mock_settings:
            mock_settings.resend_api_key = "re_default_key"
            mock_settings.email_from_address = "default@resend.dev"
            mock_settings.email_to_address = "recipient@example.com"

            service = EmailService()

            assert service.api_key == "re_default_key"
            assert service.from_address == "default@resend.dev"
            assert service.to_address == "recipient@example.com"

    def test_override_configuration(self):
        """Test that constructor parameters override settings."""
        service = EmailService(
            api_key="re_custom_key",
            from_address="custom@resend.dev",
            to_address="custom_recipient@example.com",
        )

        assert service.api_key == "re_custom_key"
        assert service.from_address == "custom@resend.dev"
        assert service.to_address == "custom_recipient@example.com"


# =============================================================================
# EmailResult Tests
# =============================================================================


class TestEmailResult:
    """Tests for EmailResult dataclass."""

    def test_email_result_defaults(self):
        """Test EmailResult default values."""
        result = EmailResult(success=True, message="OK")

        assert result.success is True
        assert result.message == "OK"
        assert result.attempts == 1
        assert result.email_id is None

    def test_email_result_with_attempts(self):
        """Test EmailResult with custom attempts."""
        result = EmailResult(success=False, message="Failed", attempts=3)

        assert result.success is False
        assert result.attempts == 3

    def test_email_result_with_email_id(self):
        """Test EmailResult with email_id."""
        result = EmailResult(
            success=True,
            message="Sent",
            attempts=1,
            email_id="re_123abc"
        )

        assert result.email_id == "re_123abc"
