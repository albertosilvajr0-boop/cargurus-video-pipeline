"""Email notification system for video completion events.

Sends email notifications when videos are ready, with download links
and social sharing buttons. Supports SMTP and SendGrid.

Configuration via environment variables:
  NOTIFICATION_ENABLED=true
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=your@email.com
  SMTP_PASSWORD=app-password
  NOTIFICATION_FROM=noreply@yourdealership.com
  NOTIFICATION_TO=manager@dealership.com  (default recipient)

  # Or use SendGrid:
  SENDGRID_API_KEY=SG.xxx
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from utils.logger import get_logger

logger = get_logger("notifications")

# Configuration
NOTIFICATION_ENABLED = os.environ.get("NOTIFICATION_ENABLED", "false").lower() == "true"
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
NOTIFICATION_FROM = os.environ.get("NOTIFICATION_FROM", SMTP_USER)
NOTIFICATION_TO = os.environ.get("NOTIFICATION_TO", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")


def _caption_block(caption: str) -> str:
    """Build the HTML caption block for email notifications."""
    if not caption:
        return ""
    return (
        '<div style="background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 8px; '
        'padding: 16px; margin-bottom: 24px;">'
        '<p style="color: #71717a; font-size: 12px; margin-bottom: 4px;">CAPTION (ready to copy)</p>'
        f'<p style="font-size: 14px;">{caption}</p></div>'
    )


def send_video_ready_email(
    vehicle_name: str,
    video_url: str,
    recipient: str | None = None,
    vehicle_id: int | None = None,
    caption: str = "",
):
    """Send an email notification that a video is ready.

    Args:
        vehicle_name: e.g. "2024 RAM 1500 Laramie"
        video_url: Public URL to the video
        recipient: Email address (uses NOTIFICATION_TO default if not provided)
        vehicle_id: For building share links
        caption: Social media caption
    """
    if not NOTIFICATION_ENABLED:
        logger.debug("Notifications disabled, skipping email for %s", vehicle_name)
        return False

    to_email = recipient or NOTIFICATION_TO
    if not to_email:
        logger.warning("No recipient for video notification email")
        return False

    subject = f"Video Ready: {vehicle_name}"

    # Build HTML email
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; background: #0f1117; color: #e4e4e7; padding: 32px; border-radius: 12px;">
        <h1 style="color: #3b82f6; font-size: 24px; margin-bottom: 8px;">Your Video is Ready!</h1>
        <h2 style="color: #e4e4e7; font-size: 18px; font-weight: 400; margin-bottom: 24px;">{vehicle_name}</h2>

        <div style="background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
            <a href="{video_url}" style="display: inline-block; background: #3b82f6; color: white; padding: 12px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">
                Watch & Download Video
            </a>
        </div>

        {_caption_block(caption)}

        <div style="margin-top: 24px; border-top: 1px solid #2a2d3a; padding-top: 16px;">
            <p style="color: #71717a; font-size: 12px;">Share this video:</p>
            <div style="display: flex; gap: 12px; margin-top: 8px;">
                <a href="https://www.facebook.com/sharer/sharer.php?u={video_url}" style="color: #3b82f6; text-decoration: none; font-size: 14px;">Facebook</a>
                <a href="https://twitter.com/intent/tweet?url={video_url}" style="color: #3b82f6; text-decoration: none; font-size: 14px;">Twitter</a>
                <a href="https://wa.me/?text={video_url}" style="color: #3b82f6; text-decoration: none; font-size: 14px;">WhatsApp</a>
            </div>
        </div>

        <p style="color: #71717a; font-size: 11px; margin-top: 32px; text-align: center;">
            Vehicle Video Pipeline — Automated AI Video Generation
        </p>
    </div>
    """

    # Try SendGrid first, then SMTP
    if SENDGRID_API_KEY:
        return _send_via_sendgrid(to_email, subject, html)
    elif SMTP_USER and SMTP_PASSWORD:
        return _send_via_smtp(to_email, subject, html)
    else:
        logger.warning("No email provider configured (SMTP or SendGrid)")
        return False


def send_batch_complete_email(
    batch_id: str,
    total: int,
    completed: int,
    failed: int,
    recipient: str | None = None,
):
    """Send notification when a batch job completes."""
    if not NOTIFICATION_ENABLED:
        return False

    to_email = recipient or NOTIFICATION_TO
    if not to_email:
        return False

    subject = f"Batch Complete: {completed}/{total} videos generated"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto; background: #0f1117; color: #e4e4e7; padding: 32px; border-radius: 12px;">
        <h1 style="color: #22c55e; font-size: 24px;">Batch Complete</h1>
        <div style="display: flex; gap: 24px; margin: 24px 0;">
            <div style="text-align: center;"><div style="font-size: 32px; font-weight: 700; color: #22c55e;">{completed}</div><div style="color: #71717a; font-size: 12px;">Completed</div></div>
            <div style="text-align: center;"><div style="font-size: 32px; font-weight: 700; color: #ef4444;">{failed}</div><div style="color: #71717a; font-size: 12px;">Failed</div></div>
            <div style="text-align: center;"><div style="font-size: 32px; font-weight: 700; color: #3b82f6;">{total}</div><div style="color: #71717a; font-size: 12px;">Total</div></div>
        </div>
        <p style="color: #71717a; font-size: 12px;">Batch ID: {batch_id}</p>
    </div>
    """

    if SENDGRID_API_KEY:
        return _send_via_sendgrid(to_email, subject, html)
    elif SMTP_USER and SMTP_PASSWORD:
        return _send_via_smtp(to_email, subject, html)
    return False


def _send_via_smtp(to_email: str, subject: str, html_body: str) -> bool:
    """Send email via SMTP (Gmail, Outlook, etc.)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = NOTIFICATION_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(NOTIFICATION_FROM, to_email, msg.as_string())

        logger.info("Email sent to %s: %s", to_email, subject)
        return True
    except Exception as e:
        logger.error("SMTP email failed: %s", e)
        return False


def _send_via_sendgrid(to_email: str, subject: str, html_body: str) -> bool:
    """Send email via SendGrid API."""
    try:
        import httpx
        response = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": NOTIFICATION_FROM},
                "subject": subject,
                "content": [{"type": "text/html", "value": html_body}],
            },
            timeout=10,
        )
        if response.status_code in (200, 201, 202):
            logger.info("SendGrid email sent to %s: %s", to_email, subject)
            return True
        else:
            logger.error("SendGrid failed (%d): %s", response.status_code, response.text[:200])
            return False
    except Exception as e:
        logger.error("SendGrid email failed: %s", e)
        return False
