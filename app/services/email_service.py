"""
Email Service for YouTube Digest

Handles:
- Sending digest emails via SMTP
- HTML + Plain text multipart messages
- Connection testing
- Retry logic with exponential backoff
"""
import logging
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]  # Seconds between retries

# Connection settings
SMTP_TIMEOUT = 30  # Seconds

# Validation limits
MAX_SUBJECT_LENGTH = 998  # RFC 5321 limit
MAX_EMAIL_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class EmailResult:
    """Result of sending an email."""

    success: bool
    message: str
    attempts: int = 1


# =============================================================================
# Service Class
# =============================================================================


class EmailService:
    """Service for sending emails via SMTP."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        from_address: Optional[str] = None,
        to_address: Optional[str] = None,
    ):
        """
        Initialize the email service.

        Args:
            host: SMTP host (uses settings if not provided)
            port: SMTP port (uses settings if not provided)
            user: SMTP username (uses settings if not provided)
            password: SMTP password (uses settings if not provided)
            from_address: Sender email (uses settings if not provided)
            to_address: Recipient email (uses settings if not provided)
        """
        self.host = host or settings.smtp_host
        self.port = port or settings.smtp_port
        self.user = user or settings.smtp_user
        self.password = password or settings.smtp_password
        self.from_address = from_address or settings.smtp_from_address
        self.to_address = to_address or settings.smtp_to_address

    def _create_message(
        self,
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> MIMEMultipart:
        """
        Create a multipart email message.

        Args:
            subject: Email subject
            html_content: HTML body
            plain_content: Plain text body (fallback)

        Returns:
            MIMEMultipart message
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = self.to_address

        # Attach plain text first (fallback), then HTML (preferred)
        part_text = MIMEText(plain_content, "plain", "utf-8")
        part_html = MIMEText(html_content, "html", "utf-8")

        msg.attach(part_text)
        msg.attach(part_html)

        return msg

    def _send_with_retry(self, msg: MIMEMultipart) -> EmailResult:
        """
        Send email with retry logic.

        Args:
            msg: MIMEMultipart message to send

        Returns:
            EmailResult with success status and message
        """
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                logger.info(
                    f"Sending email attempt {attempt + 1}/{MAX_RETRIES} "
                    f"to {self.to_address}"
                )

                # Create SSL context
                context = ssl.create_default_context()

                # Connect and send
                with smtplib.SMTP(
                    self.host, self.port, timeout=SMTP_TIMEOUT
                ) as server:
                    # Set socket timeout for all operations after connect
                    if server.sock:
                        server.sock.settimeout(SMTP_TIMEOUT)
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.user, self.password)
                    server.send_message(msg)

                logger.info(f"Email sent successfully to {self.to_address}")
                return EmailResult(
                    success=True,
                    message="Email sent successfully",
                    attempts=attempt + 1,
                )

            except smtplib.SMTPAuthenticationError as e:
                # Don't retry auth errors
                logger.error(f"SMTP authentication failed: {e}")
                return EmailResult(
                    success=False,
                    message=f"Authentication failed: {e}",
                    attempts=attempt + 1,
                )

            except smtplib.SMTPRecipientsRefused as e:
                # Don't retry recipient errors
                logger.error(f"Recipient refused: {e}")
                return EmailResult(
                    success=False,
                    message=f"Recipient refused: {e}",
                    attempts=attempt + 1,
                )

            except (smtplib.SMTPException, OSError, TimeoutError) as e:
                last_error = e
                logger.warning(
                    f"Email send attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                )

                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)

        # All retries exhausted
        error_msg = f"Failed to send email after {MAX_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        return EmailResult(
            success=False,
            message=error_msg,
            attempts=MAX_RETRIES,
        )

    def send_digest(
        self,
        html_content: str,
        plain_content: str,
        subject: str,
    ) -> EmailResult:
        """
        Send a digest email.

        Args:
            html_content: HTML body of the email
            plain_content: Plain text fallback
            subject: Email subject

        Returns:
            EmailResult with success status
        """
        if not self.user or not self.password:
            return EmailResult(
                success=False,
                message="SMTP credentials not configured",
            )

        # Validate subject length
        if len(subject) > MAX_SUBJECT_LENGTH:
            return EmailResult(
                success=False,
                message=f"Subject too long ({len(subject)} > {MAX_SUBJECT_LENGTH} chars)",
            )

        # Validate email size
        total_size = len(html_content.encode("utf-8")) + len(plain_content.encode("utf-8"))
        if total_size > MAX_EMAIL_SIZE_BYTES:
            return EmailResult(
                success=False,
                message=f"Email too large ({total_size} > {MAX_EMAIL_SIZE_BYTES} bytes)",
            )

        msg = self._create_message(
            subject=subject,
            html_content=html_content,
            plain_content=plain_content,
        )

        return self._send_with_retry(msg)

    def send_test_email(self) -> EmailResult:
        """
        Send a test email to verify configuration.

        Returns:
            EmailResult with success status
        """
        subject = "YouTube Digest - Test Email"
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1 style="color: #17214B;">YouTube Digest Test</h1>
            <p>Dies ist eine Test-E-Mail vom YouTube Digest System.</p>
            <p style="color: #22c55e; font-weight: bold;">
                ✓ SMTP-Verbindung funktioniert!
            </p>
            <hr style="border: 1px solid #e5e5e5; margin: 20px 0;">
            <p style="color: #737373; font-size: 12px;">
                Gesendet von: {from_addr}<br>
                An: {to_addr}<br>
                Server: {host}:{port}
            </p>
        </body>
        </html>
        """.format(
            from_addr=self.from_address,
            to_addr=self.to_address,
            host=self.host,
            port=self.port,
        )

        plain_content = f"""
YouTube Digest Test

Dies ist eine Test-E-Mail vom YouTube Digest System.

✓ SMTP-Verbindung funktioniert!

---
Gesendet von: {self.from_address}
An: {self.to_address}
Server: {self.host}:{self.port}
        """.strip()

        logger.info("Sending test email...")
        return self.send_digest(
            html_content=html_content,
            plain_content=plain_content,
            subject=subject,
        )

    def test_connection(self) -> EmailResult:
        """
        Test SMTP connection without sending an email.

        Returns:
            EmailResult with success status
        """
        if not self.user or not self.password:
            return EmailResult(
                success=False,
                message="SMTP credentials not configured",
            )

        try:
            logger.info(f"Testing SMTP connection to {self.host}:{self.port}")

            context = ssl.create_default_context()

            with smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT) as server:
                # Set socket timeout for all operations after connect
                if server.sock:
                    server.sock.settimeout(SMTP_TIMEOUT)
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self.user, self.password)
                # Don't send anything, just verify connection

            logger.info("SMTP connection test successful")
            return EmailResult(
                success=True,
                message=f"Connection to {self.host}:{self.port} successful",
            )

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return EmailResult(
                success=False,
                message=f"Authentication failed: {e}",
            )

        except (smtplib.SMTPException, OSError, TimeoutError) as e:
            logger.error(f"SMTP connection failed: {e}")
            return EmailResult(
                success=False,
                message=f"Connection failed: {e}",
            )


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Email Service CLI")
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Test SMTP connection only",
    )
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="Send a test email",
    )
    parser.add_argument(
        "--to",
        type=str,
        help="Override recipient email",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # Create service
    service = EmailService()
    if args.to:
        service.to_address = args.to

    if args.test_connection:
        print(f"Testing connection to {service.host}:{service.port}...")
        result = service.test_connection()
        print(f"\n{'✓' if result.success else '✗'} {result.message}")

    elif args.send_test:
        print(f"Sending test email to {service.to_address}...")
        result = service.send_test_email()
        print(f"\n{'✓' if result.success else '✗'} {result.message}")
        if result.attempts > 1:
            print(f"  (took {result.attempts} attempts)")

    else:
        print("Usage:")
        print("  --test-connection  Test SMTP connection")
        print("  --send-test        Send a test email")
        print("  --to EMAIL         Override recipient")
