"""
Email Service for YouTube Digest

Handles:
- Sending digest emails via Resend API
- HTML + Plain text multipart messages
- Connection testing
- Retry logic with exponential backoff
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

import resend

from app.config import settings

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]  # Seconds between retries

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
    email_id: Optional[str] = None


# =============================================================================
# Service Class
# =============================================================================


class EmailService:
    """Service for sending emails via Resend API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_address: Optional[str] = None,
        to_address: Optional[str] = None,
    ):
        """
        Initialize the email service.

        Args:
            api_key: Resend API key (uses settings if not provided)
            from_address: Sender email (uses settings if not provided)
            to_address: Recipient email (uses settings if not provided)
        """
        self.api_key = api_key or settings.resend_api_key
        self.from_address = from_address or settings.email_from_address
        self.to_address = to_address or settings.email_to_address

        # Configure resend
        if self.api_key:
            resend.api_key = self.api_key

    def _send_with_retry(
        self,
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> EmailResult:
        """
        Send email with retry logic.

        Args:
            subject: Email subject
            html_content: HTML body
            plain_content: Plain text fallback

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

                params = {
                    "from": self.from_address,
                    "to": [self.to_address],
                    "subject": subject,
                    "html": html_content,
                    "text": plain_content,
                }

                response = resend.Emails.send(params)

                email_id = response.get("id") if isinstance(response, dict) else None

                logger.info(f"Email sent successfully to {self.to_address}, id={email_id}")
                return EmailResult(
                    success=True,
                    message="Email sent successfully",
                    attempts=attempt + 1,
                    email_id=email_id,
                )

            except resend.exceptions.ResendError as e:
                # Check for non-retryable errors
                error_str = str(e).lower()
                if "invalid" in error_str or "unauthorized" in error_str:
                    logger.error(f"Resend API error (non-retryable): {e}")
                    return EmailResult(
                        success=False,
                        message=f"API error: {e}",
                        attempts=attempt + 1,
                    )

                last_error = e
                logger.warning(
                    f"Email send attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                )

                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)

            except Exception as e:
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
        if not self.api_key:
            return EmailResult(
                success=False,
                message="Resend API key not configured",
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

        return self._send_with_retry(
            subject=subject,
            html_content=html_content,
            plain_content=plain_content,
        )

    def send_test_email(self) -> EmailResult:
        """
        Send a test email to verify configuration.

        Returns:
            EmailResult with success status
        """
        subject = "YouTube Digest - Test Email"
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1 style="color: #17214B;">YouTube Digest Test</h1>
            <p>Dies ist eine Test-E-Mail vom YouTube Digest System.</p>
            <p style="color: #22c55e; font-weight: bold;">
                ✓ Resend API funktioniert!
            </p>
            <hr style="border: 1px solid #e5e5e5; margin: 20px 0;">
            <p style="color: #737373; font-size: 12px;">
                Gesendet von: {self.from_address}<br>
                An: {self.to_address}<br>
                Service: Resend API
            </p>
        </body>
        </html>
        """

        plain_content = f"""
YouTube Digest Test

Dies ist eine Test-E-Mail vom YouTube Digest System.

✓ Resend API funktioniert!

---
Gesendet von: {self.from_address}
An: {self.to_address}
Service: Resend API
        """.strip()

        logger.info("Sending test email...")
        return self.send_digest(
            html_content=html_content,
            plain_content=plain_content,
            subject=subject,
        )

    def test_connection(self) -> EmailResult:
        """
        Test Resend API connection by checking API key validity.

        Returns:
            EmailResult with success status
        """
        if not self.api_key:
            return EmailResult(
                success=False,
                message="Resend API key not configured",
            )

        try:
            logger.info("Testing Resend API connection...")

            # Try to list domains to verify API key works
            # This is a lightweight call that verifies auth
            resend.api_key = self.api_key
            domains = resend.Domains.list()

            logger.info("Resend API connection test successful")
            return EmailResult(
                success=True,
                message=f"Resend API connection successful ({len(domains.get('data', []))} domains configured)",
            )

        except resend.exceptions.ResendError as e:
            logger.error(f"Resend API connection failed: {e}")
            return EmailResult(
                success=False,
                message=f"API connection failed: {e}",
            )

        except Exception as e:
            logger.error(f"Resend API connection failed: {e}")
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
        help="Test Resend API connection",
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
        print("Testing Resend API connection...")
        result = service.test_connection()
        print(f"\n{'✓' if result.success else '✗'} {result.message}")

    elif args.send_test:
        print(f"Sending test email to {service.to_address}...")
        result = service.send_test_email()
        print(f"\n{'✓' if result.success else '✗'} {result.message}")
        if result.email_id:
            print(f"  Email ID: {result.email_id}")
        if result.attempts > 1:
            print(f"  (took {result.attempts} attempts)")

    else:
        print("Usage:")
        print("  --test-connection  Test Resend API connection")
        print("  --send-test        Send a test email")
        print("  --to EMAIL         Override recipient")
