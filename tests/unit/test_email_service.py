"""
Unit tests for Email Service.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from unittest.mock import Mock, patch, MagicMock

import pytest

from app.services.email_service import (
    EmailResult,
    EmailService,
    MAX_EMAIL_SIZE_BYTES,
    MAX_RETRIES,
    MAX_SUBJECT_LENGTH,
    SMTP_TIMEOUT,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def email_service():
    """Create an EmailService instance with test configuration."""
    return EmailService(
        host="smtp.test.com",
        port=587,
        user="test_user",
        password="test_password",
        from_address="from@test.com",
        to_address="to@test.com",
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
# Message Creation Tests
# =============================================================================


class TestCreateMessage:
    """Tests for _create_message method."""

    def test_create_message_structure(self, email_service, sample_html, sample_plain_text):
        """Test that message has correct structure."""
        msg = email_service._create_message(
            subject="Test Subject",
            html_content=sample_html,
            plain_content=sample_plain_text,
        )

        assert isinstance(msg, MIMEMultipart)
        assert msg["Subject"] == "Test Subject"
        assert msg["From"] == "from@test.com"
        assert msg["To"] == "to@test.com"

    def test_create_message_multipart(self, email_service, sample_html, sample_plain_text):
        """Test that message is multipart/alternative."""
        msg = email_service._create_message(
            subject="Test",
            html_content=sample_html,
            plain_content=sample_plain_text,
        )

        assert msg.get_content_type() == "multipart/alternative"

    def test_create_message_has_both_parts(
        self, email_service, sample_html, sample_plain_text
    ):
        """Test that message has both plain text and HTML parts."""
        msg = email_service._create_message(
            subject="Test",
            html_content=sample_html,
            plain_content=sample_plain_text,
        )

        payloads = msg.get_payload()
        assert len(payloads) == 2

        content_types = [p.get_content_type() for p in payloads]
        assert "text/plain" in content_types
        assert "text/html" in content_types


# =============================================================================
# Send Digest Tests
# =============================================================================


class TestSendDigest:
    """Tests for send_digest method."""

    def test_send_digest_no_credentials(self, sample_html, sample_plain_text):
        """Test that missing credentials returns failure."""
        service = EmailService(
            host="smtp.test.com",
            port=587,
            user="",  # No user
            password="",  # No password
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

    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_digest_success(
        self, mock_smtp_class, email_service, sample_html, sample_plain_text
    ):
        """Test successful email sending."""
        # Configure mock
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test Subject",
        )

        assert result.success is True
        assert result.attempts == 1
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("test_user", "test_password")
        mock_smtp.send_message.assert_called_once()

    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_digest_auth_failure_no_retry(
        self, mock_smtp_class, email_service, sample_html, sample_plain_text
    ):
        """Test that authentication failure doesn't retry."""
        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed"
        )
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is False
        assert result.attempts == 1  # No retries for auth errors
        assert "Authentication failed" in result.message

    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_digest_recipient_refused_no_retry(
        self, mock_smtp_class, email_service, sample_html, sample_plain_text
    ):
        """Test that recipient refused doesn't retry."""
        mock_smtp = MagicMock()
        mock_smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused(
            {"to@test.com": (550, b"User not found")}
        )
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is False
        assert result.attempts == 1  # No retries for recipient errors

    @patch("app.services.email_service.time.sleep")  # Mock sleep to speed up test
    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_digest_retry_on_connection_error(
        self, mock_smtp_class, mock_sleep, email_service, sample_html, sample_plain_text
    ):
        """Test retry logic on connection errors."""
        mock_smtp = MagicMock()
        # Fail twice, then succeed
        mock_smtp.send_message.side_effect = [
            OSError("Connection refused"),
            TimeoutError("Connection timed out"),
            None,  # Success on third attempt
        ]
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.send_digest(
            html_content=sample_html,
            plain_content=sample_plain_text,
            subject="Test",
        )

        assert result.success is True
        assert result.attempts == 3
        assert mock_sleep.call_count == 2  # Slept between retries

    @patch("app.services.email_service.time.sleep")
    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_digest_all_retries_fail(
        self, mock_smtp_class, mock_sleep, email_service, sample_html, sample_plain_text
    ):
        """Test that all retries exhausted returns failure."""
        mock_smtp = MagicMock()
        mock_smtp.send_message.side_effect = OSError("Connection refused")
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

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

    def test_test_connection_no_credentials(self):
        """Test that missing credentials returns failure."""
        service = EmailService(
            host="smtp.test.com",
            port=587,
            user="",
            password="",
        )

        result = service.test_connection()

        assert result.success is False
        assert "not configured" in result.message

    @patch("app.services.email_service.smtplib.SMTP")
    def test_test_connection_success(self, mock_smtp_class, email_service):
        """Test successful connection test."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.test_connection()

        assert result.success is True
        assert "smtp.test.com:587" in result.message
        mock_smtp.ehlo.assert_called()
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once()
        # Verify no message was sent
        mock_smtp.send_message.assert_not_called()

    @patch("app.services.email_service.smtplib.SMTP")
    def test_test_connection_auth_failure(self, mock_smtp_class, email_service):
        """Test connection test with auth failure."""
        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed"
        )
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.test_connection()

        assert result.success is False
        assert "Authentication failed" in result.message

    @patch("app.services.email_service.smtplib.SMTP")
    def test_test_connection_network_error(self, mock_smtp_class, email_service):
        """Test connection test with network error."""
        mock_smtp_class.side_effect = OSError("Network unreachable")

        result = email_service.test_connection()

        assert result.success is False
        assert "Connection failed" in result.message


# =============================================================================
# Send Test Email Tests
# =============================================================================


class TestSendTestEmail:
    """Tests for send_test_email method."""

    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_test_email_success(self, mock_smtp_class, email_service):
        """Test successful test email."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        result = email_service.send_test_email()

        assert result.success is True
        mock_smtp.send_message.assert_called_once()

        # Verify the message content
        call_args = mock_smtp.send_message.call_args
        msg = call_args[0][0]
        assert "Test" in msg["Subject"]

    @patch("app.services.email_service.smtplib.SMTP")
    def test_send_test_email_contains_server_info(self, mock_smtp_class, email_service):
        """Test that test email contains server info."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

        email_service.send_test_email()

        # Get the sent message
        call_args = mock_smtp.send_message.call_args
        msg = call_args[0][0]

        # Get HTML part
        for part in msg.get_payload():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode("utf-8")
                assert "smtp.test.com" in html
                assert "587" in html
                break


# =============================================================================
# Configuration Tests
# =============================================================================


class TestConfiguration:
    """Tests for service configuration."""

    def test_default_configuration_from_settings(self):
        """Test that service uses settings defaults."""
        with patch("app.services.email_service.settings") as mock_settings:
            mock_settings.smtp_host = "default.host.com"
            mock_settings.smtp_port = 465
            mock_settings.smtp_user = "default_user"
            mock_settings.smtp_password = "default_pass"
            mock_settings.smtp_from_address = "default@example.com"
            mock_settings.smtp_to_address = "recipient@example.com"

            service = EmailService()

            assert service.host == "default.host.com"
            assert service.port == 465
            assert service.user == "default_user"
            assert service.password == "default_pass"
            assert service.from_address == "default@example.com"
            assert service.to_address == "recipient@example.com"

    def test_override_configuration(self):
        """Test that constructor parameters override settings."""
        service = EmailService(
            host="custom.host.com",
            port=2525,
            user="custom_user",
            password="custom_pass",
            from_address="custom@example.com",
            to_address="custom_recipient@example.com",
        )

        assert service.host == "custom.host.com"
        assert service.port == 2525
        assert service.user == "custom_user"
        assert service.password == "custom_pass"
        assert service.from_address == "custom@example.com"
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

    def test_email_result_with_attempts(self):
        """Test EmailResult with custom attempts."""
        result = EmailResult(success=False, message="Failed", attempts=3)

        assert result.success is False
        assert result.attempts == 3
