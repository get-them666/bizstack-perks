"""
SMS messaging module for lead notifications and two-way engagement.

Supports both Twilio and SignalWire. SignalWire's REST API is intentionally
Twilio-compatible, so this reuses the same `twilio` Python package -- just
points its API base_url at SignalWire's Space URL instead of Twilio's
default api.twilio.com. No new dependency required.
"""

import os
import logging
import sqlite3
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

logger = logging.getLogger(__name__)


class SMSNotification(BaseModel):
    """SMS message to send to a lead."""

    lead_id: int
    phone: str
    message: str = Field(..., min_length=1, max_length=1600)
    notification_type: str = Field(default="lead_update")  # lead_update, appointment, followup, offer


class TwilioSMSManager:
    """
    Manage SMS notifications for leads via Twilio OR SignalWire.

    If signalwire_space_url is provided, the underlying Twilio-compatible
    REST client is pointed at SignalWire's endpoint
    (https://<your-space>.signalwire.com) instead of Twilio's, and
    account_sid/auth_token should be your SignalWire Project ID and API
    Token respectively. Everything else (message creation, TwiML
    generation) works identically since SignalWire implements the same
    API surface.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        signalwire_space_url: Optional[str] = None,
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.signalwire_space_url = signalwire_space_url

        self.client = None
        if account_sid and auth_token:
            self.client = Client(account_sid, auth_token)
            if signalwire_space_url:
                self.client.api.base_url = self._normalize_space_url(signalwire_space_url)

    @staticmethod
    def _normalize_space_url(space_url: str) -> str:
        """Ensure the SignalWire space URL is a full https:// base URL."""
        space_url = space_url.strip().rstrip("/")
        if not space_url.startswith("http"):
            space_url = f"https://{space_url}"
        return space_url

    def is_configured(self) -> bool:
        """Check if SMS is properly configured (Twilio or SignalWire)."""
        return self.client is not None and bool(self.from_number)

    def send_sms(self, notification: SMSNotification, conn: sqlite3.Connection) -> dict:
        """
        Send an SMS to a lead.
        Returns dict with message_sid, status, and error (if any).
        """
        if not self.is_configured():
            logger.error("Twilio SMS not configured")
            return {"status": "error", "error": "Twilio SMS not configured"}

        try:
            message = self.client.messages.create(
                body=notification.message,
                from_=self.from_number,
                to=notification.phone,
            )

            # Log message in database
            conn.execute(
                """
                INSERT INTO message_events (lead_id, message_sid, direction, channel, to_number, body)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.lead_id,
                    message.sid,
                    "outbound",
                    "sms",
                    notification.phone,
                    notification.message,
                ),
            )
            conn.commit()

            logger.info(f"SMS sent to {notification.phone}: {message.sid}")
            return {
                "status": "sent",
                "message_sid": message.sid,
                "phone": notification.phone,
                "notification_type": notification.notification_type,
            }
        except Exception as e:
            logger.error(f"SMS send error: {e}")
            return {"status": "error", "error": str(e)}

    def send_bulk_sms(self, notifications: List[SMSNotification], conn: sqlite3.Connection) -> dict:
        """Send multiple SMS messages. Returns summary."""
        results = {"sent": 0, "failed": 0, "errors": []}

        for notif in notifications:
            result = self.send_sms(notif, conn)
            if result["status"] == "sent":
                results["sent"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({
                    "phone": notif.phone,
                    "error": result.get("error"),
                })

        return results

    def send_lead_discovery_notification(
        self, lead_id: int, lead_phone: str, lead_name: str, conn: sqlite3.Connection
    ) -> dict:
        """Send SMS notifying lead they were discovered as a potential opportunity."""
        message_text = (
            f"Hi {lead_name}! BizStack Perks identified your business as a potential match for growth opportunities. "
            f"Visit https://bizstack-perks.com/apply?lead_id={lead_id} to learn more. Reply STOP to opt out."
        )

        notif = SMSNotification(
            lead_id=lead_id,
            phone=lead_phone,
            message=message_text,
            notification_type="lead_discovery",
        )

        return self.send_sms(notif, conn)

    def send_offer_notification(
        self, lead_id: int, lead_phone: str, offer_description: str, conn: sqlite3.Connection
    ) -> dict:
        """Send SMS with a time-limited offer."""
        message_text = (
            f"Exclusive offer! {offer_description} "
            f"Claim now: https://bizstack-perks.com/checkout?offer=limited "
            f"Valid for 24 hours only. Reply STOP to opt out."
        )

        notif = SMSNotification(
            lead_id=lead_id,
            phone=lead_phone,
            message=message_text,
            notification_type="offer",
        )

        return self.send_sms(notif, conn)

    def send_appointment_reminder(
        self,
        lead_id: int,
        lead_phone: str,
        lead_name: str,
        appointment_time: str,
        conn: sqlite3.Connection,
    ) -> dict:
        """Send appointment reminder SMS."""
        message_text = (
            f"Hi {lead_name}, reminder: You have a callback scheduled for {appointment_time}. "
            f"Reply C to confirm or STOP to opt out."
        )

        notif = SMSNotification(
            lead_id=lead_id,
            phone=lead_phone,
            message=message_text,
            notification_type="appointment",
        )

        return self.send_sms(notif, conn)


def handle_inbound_sms(
    message_body: str, from_phone: str, message_sid: str, conn: sqlite3.Connection
) -> MessagingResponse:
    """
    Handle inbound SMS from leads.
    Log the message and respond with an auto-reply.
    """
    response = MessagingResponse()

    # Log inbound message
    conn.execute(
        """
        INSERT INTO message_events (message_sid, direction, channel, from_number, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_sid, "inbound", "sms", from_phone, message_body),
    )
    conn.commit()

    # Handle opt-out requests
    if message_body.strip().upper() in ["STOP", "UNSUBSCRIBE", "QUIT"]:
        response.message(
            f"You've been unsubscribed from BizStack Perks. You won't receive further messages. Reply START to re-subscribe."
        )
        logger.info(f"SMS opt-out from {from_phone}")
        return response

    # Handle confirmation responses
    if message_body.strip().upper() in ["YES", "Y", "CONFIRM", "C"]:
        response.message("Thank you for confirming! We'll be in touch shortly with more details.")
        logger.info(f"SMS confirmation from {from_phone}")
        return response

    # Default response
    response.message(
        "Thank you for your message. A BizStack Perks representative will review it and get back to you soon. "
        "Reply STOP to opt out."
    )

    return response


def get_sms_opt_in_link() -> str:
    """Generate a web link for SMS opt-in (to be embedded in emails/landing pages)."""
    return "/api/sms/optin"  # To be implemented in main FastAPI app


def log_sms_consent(
    conn: sqlite3.Connection, lead_id: int, phone: str, consent: bool = True
) -> None:
    """Log SMS consent for a lead."""
    conn.execute(
        """
        INSERT INTO message_events (lead_id, direction, channel, from_number, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            "consent",
            "sms",
            phone,
            f"SMS consent: {'accepted' if consent else 'declined'}",
        ),
    )
    conn.commit()
