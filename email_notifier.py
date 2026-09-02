"""
Email delivery via SMTP (stdlib only, no paid dependency required).
Used as a fallback channel for customer portal login when SMS isn't
available or a customer prefers email.
"""

import os
import smtplib
import logging
import json
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from aws_messaging import aws_email_configured, send_email as send_ses_email

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() != "false"
AGENTMAIL_API_KEY = os.getenv("AGENTMAIL_API_KEY", "")
AGENTMAIL_INBOX_ID = os.getenv("AGENTMAIL_INBOX_ID", "")


def email_configured() -> bool:
    """Check whether AgentMail, SES, or SMTP can send mail."""
    return agentmail_configured() or aws_email_configured() or bool(
        SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM_EMAIL
    )


def agentmail_configured() -> bool:
    """Check whether the HTTPS AgentMail delivery provider is configured."""
    return bool(AGENTMAIL_API_KEY and AGENTMAIL_INBOX_ID)


def send_agentmail_email(to_email: str, subject: str, body_text: str) -> bool:
    """Send an email through AgentMail's HTTPS API."""
    payload = json.dumps(
        {"to": to_email, "subject": subject, "text": body_text}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{AGENTMAIL_INBOX_ID}/messages/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                logger.error("AgentMail returned HTTP %s for %s", response.status, to_email)
                return False
        logger.info("Email sent to %s through AgentMail", to_email)
        return True
    except urllib.error.URLError as error:
        logger.error("AgentMail failed to send email to %s: %s", to_email, error)
        return False


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """
    Send a plain-text email via SMTP. Returns True on success, False on failure.
    Never raises -- failures are logged so callers can fall back gracefully.
    """
    if not email_configured():
        logger.warning("SMTP not configured; cannot send email to %s", to_email)
        return False

    if agentmail_configured():
        return send_agentmail_email(to_email, subject, body_text)

    if aws_email_configured():
        return send_ses_email(to_email, subject, body_text)

    message = MIMEMultipart()
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message["Subject"] = subject
    message.attach(MIMEText(body_text, "plain"))

    try:
        if SMTP_USE_TLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM_EMAIL, [to_email], message.as_string())
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM_EMAIL, [to_email], message.as_string())
        logger.info("Email sent to %s", to_email)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False


def send_portal_login_code(to_email: str, code: str) -> bool:
    """Send a customer portal login code by email."""
    subject = "Your BizStack Perks login code"
    body = (
        f"Your BizStack Perks login code is: {code}\n\n"
        f"This code expires in 10 minutes. If you didn't request this, you can safely "
        f"ignore this email.\n\n"
        f"-- BizStack Perks"
    )
    return send_email(to_email, subject, body)
