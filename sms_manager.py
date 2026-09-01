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
    Handle inbound SMS from leads — synchronous wrapper used by the Twilio webhook.
    For async contexts call handle_inbound_sms_async instead.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an existing async loop (FastAPI) — use a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    handle_inbound_sms_async(message_body, from_phone, message_sid, conn),
                )
                return future.result(timeout=15)
        else:
            return loop.run_until_complete(
                handle_inbound_sms_async(message_body, from_phone, message_sid, conn)
            )
    except Exception as e:
        logger.error("SMS handler fallback error: %s", e)
        resp = MessagingResponse()
        resp.message(
            "Thanks for your message! A BizStack Perks specialist will follow up with you shortly. "
            "Reply STOP to opt out."
        )
        return resp


async def handle_inbound_sms_async(
    message_body: str, from_phone: str, message_sid: str, conn: sqlite3.Connection
) -> MessagingResponse:
    """
    AI-powered inbound SMS handler using the same Sam persona and knowledge base
    as the voice bot. Maintains per-phone conversation history for natural
    multi-turn text conversations.

    Uses OpenAI if OPENAI_API_KEY is set; falls back to smart rule-based replies.
    """
    import os
    import httpx
    import time

    response = MessagingResponse()
    body = message_body.strip()
    upper = body.upper()

    # 1. Log inbound message
    conn.execute(
        """
        INSERT OR IGNORE INTO message_events (message_sid, direction, channel, from_number, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_sid, "inbound", "sms", from_phone, body),
    )
    conn.commit()

    # 2. Hard STOP / unsubscribe — handle before anything else
    if upper in ("STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"):
        conn.execute(
            "INSERT OR IGNORE INTO outreach_unsubscribes (business_identifier) VALUES (?)",
            (from_phone,),
        )
        conn.commit()
        response.message(
            "You've been unsubscribed from BizStack Perks. You won't receive further messages. "
            "Reply START to re-subscribe."
        )
        logger.info("SMS opt-out from %s", from_phone)
        return response

    # 3. Re-subscribe
    if upper in ("START", "YES", "SUBSCRIBE"):
        response.message(
            "Welcome back to BizStack Perks! You're re-subscribed. "
            "Reply anytime with a question about financing, credit, or how we can help your business."
        )
        return response

    # 4. Build conversation history key from phone number
    # Reuse the same in-memory conversation store as the voice bot
    _SMS_CONVERSATIONS: dict = _get_sms_conversation_store()
    history = _SMS_CONVERSATIONS.get(from_phone, {}).get("messages", [])
    now = time.time()

    # Expire old SMS conversations after 2 hours of inactivity
    last_active = _SMS_CONVERSATIONS.get(from_phone, {}).get("updated_at", 0)
    if now - last_active > 7200:
        history = []

    # 5. AI response
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        try:
            # SMS-specific system prompt: same knowledge, but texting style
            public_url = os.getenv("PUBLIC_BASE_URL", "your-domain.com")
            sms_system_prompt = _build_sms_system_prompt(public_url)

            messages = [{"role": "system", "content": sms_system_prompt}]
            messages.extend(history[-10:])  # last 10 turns max
            messages.append({"role": "user", "content": body})

            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={
                        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        "messages": messages,
                        "temperature": 0.75,
                        "max_tokens": 160,  # SMS-friendly length
                    },
                )
                resp.raise_for_status()
                reply = resp.json()["choices"][0]["message"]["content"].strip()

        except Exception as e:
            logger.error("OpenAI SMS error: %s", e)
            reply = _sms_fallback_reply(body)
    else:
        reply = _sms_fallback_reply(body)

    # 6. Update conversation history
    entry = _SMS_CONVERSATIONS.setdefault(from_phone, {"messages": [], "updated_at": now})
    entry["messages"].append({"role": "user", "content": body})
    entry["messages"].append({"role": "assistant", "content": reply})
    entry["updated_at"] = now
    if len(entry["messages"]) > 20:
        entry["messages"] = entry["messages"][-20:]

    # 7. Log outbound reply
    conn.execute(
        """
        INSERT INTO message_events (direction, channel, from_number, body)
        VALUES (?, ?, ?, ?)
        """,
        ("outbound", "sms", from_phone, reply),
    )
    conn.commit()

    response.message(reply)
    return response


# In-memory SMS conversation store (separate from voice calls)
_SMS_CONV_STORE: dict = {}


def _get_sms_conversation_store() -> dict:
    return _SMS_CONV_STORE


def _build_sms_system_prompt(public_url: str) -> str:
    offer_price = os.getenv("OFFER_PRICE_DISPLAY", "$49 / month")
    return f"""You are Sam, a friendly and knowledgeable text-based representative for BizStack Perks —
a platform that helps financial services businesses (loan brokers, credit consultants, mortgage
advisors) find and convert leads. You're texting with a prospect or client.

TEXTING RULES:
- Keep replies SHORT — 1-3 sentences max. Texting is not a phone call.
- Never use bullet lists or headers. Plain conversational text only.
- Don't say "As an AI" or reveal you are a bot.
- One question max per reply. Don't pepper them with multiple questions.
- After 2-3 texts answering questions, pivot to a close: "Want me to send you the signup link?"

WHAT YOU DO:
BizStack Perks is a lead platform at {public_url}. Pricing: {offer_price}.
You help with business credit (lines of credit, SBA loans, equipment financing, MCA),
consumer credit (mortgages, personal loans, home equity, auto loans, credit cards).
When they're ready to apply, direct to {public_url}/apply.
When they want to sign up as a customer, offer to text them the signup link.

KNOWLEDGE:
- Prime rate = Fed Funds + 3%. Variable products track prime.
- Credit scores: 800+ exceptional, 740-799 very good, 700-739 good, 660-699 fair, below 660 = limited options.
- DTI under 43% = most lenders approve. DTI = monthly debt payments ÷ gross monthly income.
- SBA loans: best rates, 30-90 day process. MCA: fastest (24-48 hrs), most expensive.
- Never quote a personal rate or guarantee approval. Never accept SSNs or card numbers over text.

CLOSING: When they say "yes", "interested", "let's do it", or ask "how do I start" → 
respond: "Great — I'll send you the signup link right now." Then add: "Reply STOP anytime to opt out."

Always end with "Reply STOP to opt out." on the FIRST message to a new contact."""


def _sms_fallback_reply(body: str) -> str:
    """Rule-based SMS fallback when OpenAI is unavailable."""
    text = body.lower()
    if any(w in text for w in ["loan", "credit", "financing", "mortgage", "apply", "qualify"]):
        return (
            "Hi! BizStack Perks can help connect you with the right financing. "
            "To get started: visit our site or reply with your phone number and we'll follow up. Reply STOP to opt out."
        )
    if any(w in text for w in ["price", "cost", "how much", "pricing"]):
        offer = os.getenv("OFFER_PRICE_DISPLAY", "$49/month")
        return f"Our platform starts at {offer} and includes lead discovery, SMS automation, and analytics. Want the signup link? Reply STOP to opt out."
    if any(w in text for w in ["help", "info", "what", "tell me", "how"]):
        return (
            "BizStack Perks helps financial service businesses find and convert leads automatically. "
            "Want to learn more? Visit our site or reply and I'll help. Reply STOP to opt out."
        )
    return (
        "Thanks for reaching out to BizStack Perks! A specialist will follow up shortly. "
        "Reply STOP to opt out."
    )


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
