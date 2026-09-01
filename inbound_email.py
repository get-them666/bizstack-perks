"""
Inbound Email Handler — two complementary approaches:

1. IMAP Polling (imap_poll_inbox): periodic polling of an email inbox.
   Works with any IMAP provider (Gmail, Outlook, custom).
   Call from a background task, cron, or startup loop.

2. Webhook Endpoint (/api/email/inbound): receives inbound email events
   from providers that push email via HTTP webhook (SendGrid Inbound Parse,
   Postmark Inbound, Mailgun Inbound). Zero polling, zero latency.

Both methods parse the incoming email and route it to the right handler:
  - Replies to pipeline drafts → attach to the intake pipeline record
  - Lead inquiries → create a lead record
  - Client messages → log in message_events table
  - Auto-detect "STOP" / unsubscribe requests → honor immediately
  - Catch-all → log to inbound_emails table for admin review

Required env vars:
  IMAP polling:
    IMAP_HOST, IMAP_PORT (default 993), IMAP_USERNAME, IMAP_PASSWORD
    IMAP_FOLDER (default INBOX), IMAP_POLL_INTERVAL_SECONDS (default 60)

  Webhook mode (SendGrid / Postmark / Mailgun — all send JSON or form data):
    INBOUND_EMAIL_WEBHOOK_SECRET (optional shared secret for verification)
"""

import os
import re
import email
import imaplib
import sqlite3
import logging
import asyncio
import email.header
from datetime import datetime
from email.utils import parseaddr
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
IMAP_HOST = os.getenv("IMAP_HOST", "")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USERNAME = os.getenv("IMAP_USERNAME", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
IMAP_POLL_INTERVAL = int(os.getenv("IMAP_POLL_INTERVAL_SECONDS", "60"))
INBOUND_WEBHOOK_SECRET = os.getenv("INBOUND_EMAIL_WEBHOOK_SECRET", "")


def imap_configured() -> bool:
    return bool(IMAP_HOST and IMAP_USERNAME and IMAP_PASSWORD)


# ── DB init ────────────────────────────────────────────────────────────────────
def init_inbound_email_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            from_email TEXT,
            from_name TEXT,
            to_email TEXT,
            subject TEXT,
            body_text TEXT,
            body_html TEXT,
            raw_headers TEXT,
            routed_to TEXT,
            routed_id INTEGER,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


# ── Email parsing utilities ────────────────────────────────────────────────────
def _decode_header_value(value: str) -> str:
    """Decode RFC 2047-encoded email header value."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return "".join(decoded)


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    """Extract plain text and HTML bodies from a parsed email message."""
    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cte = str(part.get("Content-Transfer-Encoding", "")).lower()
            if ct == "text/plain" and not text_body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text_body = payload.decode(charset, errors="replace")
            elif ct == "text/html" and not html_body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = body
            else:
                text_body = body

    return text_body.strip(), html_body.strip()


def parse_email_message(raw: bytes) -> Dict[str, Any]:
    """Parse a raw email bytes blob into a structured dict."""
    msg = email.message_from_bytes(raw)

    from_raw = msg.get("From", "")
    from_name, from_email = parseaddr(from_raw)
    from_name = _decode_header_value(from_name)
    from_email = from_email.lower().strip()

    to_raw = msg.get("To", "")
    _, to_email = parseaddr(to_raw)
    to_email = to_email.lower().strip()

    subject = _decode_header_value(msg.get("Subject", ""))
    message_id = msg.get("Message-ID", "").strip()
    in_reply_to = msg.get("In-Reply-To", "").strip()
    references = msg.get("References", "").strip()

    text_body, html_body = _extract_body(msg)

    return {
        "message_id": message_id,
        "from_email": from_email,
        "from_name": from_name,
        "to_email": to_email,
        "subject": subject,
        "in_reply_to": in_reply_to,
        "references": references,
        "body_text": text_body,
        "body_html": html_body,
    }


# ── Message routing logic ──────────────────────────────────────────────────────
def _is_unsubscribe_request(parsed: Dict[str, Any]) -> bool:
    """Detect STOP / unsubscribe / opt-out requests."""
    text = (parsed.get("body_text", "") + " " + parsed.get("subject", "")).lower()
    stop_patterns = [
        r"\bstop\b", r"\bunsubscribe\b", r"\bopt.?out\b",
        r"\bremove me\b", r"\bdo not contact\b", r"\bno more emails\b",
    ]
    return any(re.search(pat, text) for pat in stop_patterns)


def _looks_like_pipeline_reply(parsed: Dict[str, Any]) -> bool:
    """Check if this email looks like a reply to a pipeline draft we sent."""
    subject = parsed.get("subject", "").lower()
    return "rate & market analysis" in subject or "bizstack perks" in subject.lower()


def _looks_like_lead_inquiry(parsed: Dict[str, Any]) -> bool:
    """Check if this email looks like a new financing inquiry."""
    text = (parsed.get("body_text", "") + " " + parsed.get("subject", "")).lower()
    lead_keywords = [
        "loan", "credit", "financing", "mortgage", "business line",
        "equipment", "interested in", "apply", "application",
    ]
    return any(kw in text for kw in lead_keywords)


def route_inbound_email(
    conn: sqlite3.Connection,
    parsed: Dict[str, Any],
) -> tuple[str, Optional[int]]:
    """
    Route an inbound email to the right handler.
    Returns (route_name, related_id_or_None).
    """
    # 1. Handle STOP / unsubscribe first
    if _is_unsubscribe_request(parsed):
        conn.execute(
            """
            INSERT OR IGNORE INTO outreach_unsubscribes (business_identifier)
            VALUES (?)
            """,
            (parsed["from_email"],),
        )
        conn.commit()
        logger.info("Inbound email: unsubscribe request from %s", parsed["from_email"])
        return "unsubscribe", None

    # 2. Check if it's a reply to a pipeline draft
    if _looks_like_pipeline_reply(parsed):
        # Try to find a matching pipeline item by client email
        row = conn.execute(
            "SELECT id FROM intake_pipeline WHERE client_email = ? ORDER BY created_at DESC LIMIT 1",
            (parsed["from_email"],),
        ).fetchone()
        if row:
            logger.info("Inbound email: pipeline reply from %s, linked to pipeline #%s", parsed["from_email"], row["id"])
            return "pipeline_reply", row["id"]

    # 3. Check if it looks like a new lead inquiry
    if _looks_like_lead_inquiry(parsed):
        logger.info("Inbound email: lead inquiry from %s", parsed["from_email"])
        return "lead_inquiry", None

    # 4. Check if the sender is a known customer
    customer = conn.execute(
        "SELECT id FROM customers WHERE email = ?", (parsed["from_email"],)
    ).fetchone()
    if customer:
        return "customer_message", customer["id"]

    # 5. Catch-all: log for admin review
    return "catch_all", None


def store_inbound_email(
    conn: sqlite3.Connection,
    parsed: Dict[str, Any],
    route: str,
    routed_id: Optional[int],
) -> int:
    """Persist a parsed inbound email to the inbound_emails table."""
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO inbound_emails
                (message_id, from_email, from_name, to_email, subject,
                 body_text, body_html, routed_to, routed_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed.get("message_id"),
                parsed.get("from_email"),
                parsed.get("from_name"),
                parsed.get("to_email"),
                parsed.get("subject"),
                parsed.get("body_text"),
                parsed.get("body_html"),
                route,
                routed_id,
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0
    except Exception as e:
        logger.error("Failed to store inbound email: %s", e)
        return 0


# ── IMAP polling ───────────────────────────────────────────────────────────────
def poll_imap_inbox(conn: sqlite3.Connection) -> int:
    """
    Poll IMAP inbox for new (UNSEEN) messages, parse, route, and mark as seen.
    Returns the number of messages processed.

    This is a synchronous function — wrap it in asyncio.to_thread() for
    use in an async context, or call from a background thread/process.
    """
    if not imap_configured():
        logger.warning("IMAP not configured; skipping inbox poll. Set IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD.")
        return 0

    processed = 0
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USERNAME, IMAP_PASSWORD)
        mail.select(IMAP_FOLDER)

        # Search for UNSEEN messages
        status, msg_ids = mail.search(None, "UNSEEN")
        if status != "OK" or not msg_ids[0]:
            mail.logout()
            return 0

        ids = msg_ids[0].split()
        logger.info("IMAP poll: %d new messages in %s", len(ids), IMAP_FOLDER)

        for msg_id in ids:
            try:
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw = msg_data[0][1]
                parsed = parse_email_message(raw)

                # Skip empty or system messages
                if not parsed.get("from_email"):
                    continue

                route, routed_id = route_inbound_email(conn, parsed)
                store_inbound_email(conn, parsed, route, routed_id)

                # Mark as seen (don't delete — let the mailbox retain it)
                mail.store(msg_id, "+FLAGS", "\\Seen")
                processed += 1

            except Exception as e:
                logger.error("Error processing IMAP message %s: %s", msg_id, e)

        mail.logout()
    except imaplib.IMAP4.error as e:
        logger.error("IMAP connection error: %s", e)
    except Exception as e:
        logger.error("IMAP poll failed: %s", e)

    return processed


async def poll_imap_async(conn_factory) -> int:
    """
    Async wrapper around poll_imap_inbox using a thread pool.
    conn_factory is a callable that returns a sqlite3.Connection.
    """
    def _poll():
        conn = conn_factory()
        try:
            return poll_imap_inbox(conn)
        finally:
            conn.close()

    return await asyncio.to_thread(_poll)


# ── Background poller (run as FastAPI background task) ────────────────────────
_polling_active = False


async def start_imap_background_poller(conn_factory, interval_seconds: int = IMAP_POLL_INTERVAL):
    """
    Run IMAP polling in a continuous background loop.
    Call once on startup from the FastAPI lifespan handler.
    
    Example usage in main.py lifespan:
        asyncio.create_task(start_imap_background_poller(
            lambda: sqlite3.connect(DATABASE_PATH)
        ))
    """
    global _polling_active
    if _polling_active:
        logger.warning("IMAP background poller already running; not starting a second instance.")
        return

    if not imap_configured():
        logger.info("IMAP not configured — background poller not started. Set IMAP_HOST/USERNAME/PASSWORD to enable.")
        return

    _polling_active = True
    logger.info("IMAP background poller started (interval: %ds, folder: %s)", interval_seconds, IMAP_FOLDER)

    while True:
        try:
            count = await poll_imap_async(conn_factory)
            if count:
                logger.info("IMAP poll: processed %d messages", count)
        except Exception as e:
            logger.error("IMAP background poller error: %s", e)
        await asyncio.sleep(interval_seconds)


# ── Webhook payload parsers (SendGrid / Postmark / Mailgun) ────────────────────
def parse_sendgrid_inbound(form_data: dict) -> Dict[str, Any]:
    """
    Parse a SendGrid Inbound Parse webhook payload.
    Docs: https://docs.sendgrid.com/for-developers/parsing-email/inbound-email
    """
    return {
        "message_id": form_data.get("headers", "").split("Message-ID:")[1].split("\n")[0].strip() if "Message-ID:" in form_data.get("headers", "") else "",
        "from_email": parseaddr(form_data.get("from", ""))[1].lower(),
        "from_name": parseaddr(form_data.get("from", ""))[0],
        "to_email": parseaddr(form_data.get("to", ""))[1].lower(),
        "subject": form_data.get("subject", ""),
        "in_reply_to": "",
        "body_text": form_data.get("text", ""),
        "body_html": form_data.get("html", ""),
    }


def parse_postmark_inbound(json_data: dict) -> Dict[str, Any]:
    """
    Parse a Postmark inbound webhook payload.
    Docs: https://postmarkapp.com/developer/webhooks/inbound-webhook
    """
    from_raw = json_data.get("From", "")
    from_name, from_email = parseaddr(from_raw)
    to_raw = (json_data.get("To") or [{}])
    if isinstance(to_raw, list) and to_raw:
        to_email = to_raw[0].get("Email", "").lower()
    else:
        _, to_email = parseaddr(str(to_raw))
        to_email = to_email.lower()

    return {
        "message_id": json_data.get("MessageID", ""),
        "from_email": from_email.lower(),
        "from_name": from_name,
        "to_email": to_email,
        "subject": json_data.get("Subject", ""),
        "in_reply_to": json_data.get("ReplyTo", ""),
        "body_text": json_data.get("TextBody", ""),
        "body_html": json_data.get("HtmlBody", ""),
    }


def parse_mailgun_inbound(form_data: dict) -> Dict[str, Any]:
    """
    Parse a Mailgun Routes/Receive webhook payload.
    Docs: https://documentation.mailgun.com/en/latest/user_manual.html#routes
    """
    return {
        "message_id": form_data.get("Message-Id", ""),
        "from_email": parseaddr(form_data.get("sender", form_data.get("from", "")))[1].lower(),
        "from_name": parseaddr(form_data.get("from", ""))[0],
        "to_email": parseaddr(form_data.get("recipient", form_data.get("to", "")))[1].lower(),
        "subject": form_data.get("subject", ""),
        "in_reply_to": form_data.get("In-Reply-To", ""),
        "body_text": form_data.get("body-plain", ""),
        "body_html": form_data.get("body-html", ""),
    }


def get_inbound_emails(
    conn: sqlite3.Connection,
    limit: int = 100,
    route_filter: Optional[str] = None,
) -> List[sqlite3.Row]:
    if route_filter:
        return conn.execute(
            "SELECT * FROM inbound_emails WHERE routed_to = ? ORDER BY created_at DESC LIMIT ?",
            (route_filter, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM inbound_emails ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()


# ── AI-powered email auto-reply ────────────────────────────────────────────────

async def generate_email_auto_reply(
    parsed: Dict[str, Any],
    route: str,
    pipeline_subject: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """
    Use OpenAI to draft a reply to an inbound email.
    Returns (subject, body) or None if OpenAI is not configured.

    The draft is stored for admin review — it is NOT sent automatically unless
    BOT_AUTO_SEND_EMAIL=true is set in the environment.
    """
    import os
    import httpx

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        return None

    sender_name = parsed.get("from_name") or parsed.get("from_email", "")
    first_name  = sender_name.split()[0] if sender_name else "there"
    subject_in  = parsed.get("subject", "")
    body_in     = (parsed.get("body_text") or "")[:1500]  # cap for prompt
    company     = os.getenv("SENDER_COMPANY_NAME", "BizStack Perks")
    public_url  = os.getenv("PUBLIC_BASE_URL", "your-domain.com")

    if route == "unsubscribe":
        return None  # Never auto-reply to unsubscribes

    if route == "pipeline_reply":
        context_note = (
            f"This person is replying to a rate & market analysis briefing we sent them "
            f"(original subject: {pipeline_subject or subject_in}). "
            "Acknowledge their reply warmly, answer any questions they raised, and offer to "
            "schedule a call or help them take next steps."
        )
    elif route == "lead_inquiry":
        context_note = (
            "This person is inquiring about financing or credit products. "
            "Acknowledge their interest, give a brief helpful answer to their question, "
            "and invite them to apply at the link or schedule a quick call."
        )
    elif route == "customer_message":
        context_note = (
            "This is an existing BizStack Perks customer. "
            "Respond helpfully to whatever they've asked. "
            "If they have account or billing questions, direct them to the customer portal."
        )
    else:
        context_note = (
            "This is a general inbound message. Respond professionally and helpfully. "
            "If it seems like a financing inquiry, guide them to apply or schedule a call."
        )

    prompt = f"""You are Sam, a professional and warm representative for {company} ({public_url}).
Draft a concise email reply to the message below. Be helpful, friendly, and professional.
Keep the reply to 3-5 short paragraphs. Do not use excessive bullet points.
Always end with your name and company. Never promise specific rates or credit decisions.

{context_note}

INBOUND MESSAGE:
From: {sender_name} <{parsed.get('from_email','')}>
Subject: {subject_in}

{body_in}

Return ONLY the email body text (no subject line, no headers, just the body)."""

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.6,
                },
            )
            resp.raise_for_status()
            body_out = resp.json()["choices"][0]["message"]["content"].strip()

        # Build reply subject
        subj_prefix = "Re: " if not subject_in.lower().startswith("re:") else ""
        reply_subject = f"{subj_prefix}{subject_in}" if subject_in else f"Re: Your message to {company}"

        return reply_subject, body_out

    except Exception as e:
        logger.error("Email auto-reply generation failed: %s", e)
        return None


async def process_inbound_and_draft_reply(
    conn: sqlite3.Connection,
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Full inbound email processing pipeline:
      1. Route the email
      2. Store it
      3. Generate AI draft reply
      4. If BOT_AUTO_SEND_EMAIL=true AND email_configured(), send immediately
         Otherwise store draft in inbound_emails.routed_to for admin review

    Returns a dict with route, email_id, draft_subject, draft_body, sent.
    """
    import os
    from email_notifier import send_email, email_configured

    route, routed_id = route_inbound_email(conn, parsed)
    email_id = store_inbound_email(conn, parsed, route, routed_id)

    if route == "unsubscribe":
        return {"route": route, "email_id": email_id, "sent": False}

    # Look up pipeline subject if this is a pipeline reply
    pipeline_subject = None
    if route == "pipeline_reply" and routed_id:
        row = conn.execute(
            "SELECT email_subject FROM intake_pipeline WHERE id = ?", (routed_id,)
        ).fetchone()
        if row:
            pipeline_subject = row["email_subject"]

    draft = await generate_email_auto_reply(parsed, route, pipeline_subject)
    if not draft:
        return {"route": route, "email_id": email_id, "sent": False, "draft": None}

    draft_subject, draft_body = draft
    auto_send = os.getenv("BOT_AUTO_SEND_EMAIL", "false").lower() == "true"

    sent = False
    if auto_send and email_configured():
        sent = send_email(
            to_email=parsed["from_email"],
            subject=draft_subject,
            body_text=draft_body,
        )
        logger.info(
            "Auto-reply %s to %s (route=%s)",
            "SENT" if sent else "FAILED",
            parsed["from_email"],
            route,
        )
    else:
        # Store draft subject/body back on the inbound_emails row for admin review
        conn.execute(
            "UPDATE inbound_emails SET raw_headers = ? WHERE id = ?",
            (f"DRAFT_SUBJECT: {draft_subject}\n\n{draft_body}", email_id),
        )
        conn.commit()

    return {
        "route": route,
        "email_id": email_id,
        "draft_subject": draft_subject,
        "draft_body": draft_body,
        "sent": sent,
    }
