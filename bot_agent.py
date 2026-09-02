"""
bot_agent.py — BizStack Perks Autonomous Agent

The central coordinator that runs the company with ~95% automation.
Sam handles inbound/outbound calls, texts, and emails; interprets the
pipeline; and proactively follows up with leads and clients.

HOW TO RUN:
  One-shot (cron, Railway cron job, etc.):
    python bot_agent.py --run-once

  Continuous daemon (runs scheduled tasks forever):
    python bot_agent.py --daemon

  Specific task:
    python bot_agent.py --task follow-up-leads
    python bot_agent.py --task poll-email
    python bot_agent.py --task pipeline-reminders
    python bot_agent.py --task outbound-calls

CRON EXAMPLE (run every 15 minutes):
    */15 * * * * cd /app && python bot_agent.py --run-once >> /tmp/bot_agent.log 2>&1

RAILWAY CRON:
  Set up a second Railway service pointing at the same repo with:
    Start command: python bot_agent.py --daemon

ENVIRONMENT VARIABLES (all optional — features disable gracefully if unset):
  OPENAI_API_KEY          — enables AI responses for all channels
  TWILIO_ACCOUNT_SID/AUTH_TOKEN/PHONE_NUMBER  — SMS + voice
  SMTP_HOST/USERNAME/PASSWORD/FROM_EMAIL      — outbound email
  IMAP_HOST/USERNAME/PASSWORD                 — inbound email polling
  FRED_API_KEY            — live rate data in pipeline
  CENSUS_API_KEY          — demographic data in pipeline
  DATABASE_PATH           — SQLite path (default: ./bizstack.db)
  BOT_AUTO_SEND_EMAIL     — set to 'true' to send email replies without review
  BOT_FOLLOW_UP_HOURS     — hours after lead creation before follow-up (default: 2)
  BOT_MAX_FOLLOW_UPS      — max follow-up texts per lead (default: 2)
  PUBLIC_BASE_URL         — your app URL (used in follow-up message links)
"""

import os
import sys
import asyncio
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bot_agent] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot_agent")

# ── Config ─────────────────────────────────────────────────────────────────────
_VOLUME_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "/app/data")
DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    os.path.join(_VOLUME_PATH, "bizstack.db")
    if os.path.isdir(_VOLUME_PATH)
    else os.path.join(os.path.dirname(__file__), "bizstack.db"),
)
OPENAI_KEY      = os.getenv("OPENAI_API_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
FOLLOW_UP_HOURS = int(os.getenv("BOT_FOLLOW_UP_HOURS", "2"))
MAX_FOLLOW_UPS  = int(os.getenv("BOT_MAX_FOLLOW_UPS", "2"))
AUTO_SEND_EMAIL = os.getenv("BOT_AUTO_SEND_EMAIL", "false").lower() == "true"

# Twilio / SignalWire
ACCOUNT_SID  = os.getenv("SIGNALWIRE_PROJECT_ID") or os.getenv("TWILIO_ACCOUNT_SID", "")
AUTH_TOKEN   = os.getenv("SIGNALWIRE_API_TOKEN")  or os.getenv("TWILIO_AUTH_TOKEN", "")
FROM_NUMBER  = (
    os.getenv("SIGNALWIRE_PHONE_NUMBER")
    or os.getenv("TWILIO_PHONE_NUMBER")
    or os.getenv("TWILIO_NUMBER", "")
)
SPACE_URL    = os.getenv("SIGNALWIRE_SPACE_URL", "")

# SMTP
SMTP_HOST    = os.getenv("SMTP_HOST", "")
SMTP_USER    = os.getenv("SMTP_USERNAME", "")
SMTP_PASS    = os.getenv("SMTP_PASSWORD", "")

# IMAP
IMAP_HOST    = os.getenv("IMAP_HOST", "")
IMAP_USER    = os.getenv("IMAP_USERNAME", "")
IMAP_PASS    = os.getenv("IMAP_PASSWORD", "")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Ensure all tables exist (agent may run before or independent of the web app)
    try:
        from intake_pipeline import init_pipeline_tables
        from inbound_email import init_inbound_email_tables
        init_pipeline_tables(conn)
        init_inbound_email_tables(conn)
    except Exception:
        pass
    return conn


def sms_ready() -> bool:
    return bool(ACCOUNT_SID and AUTH_TOKEN and FROM_NUMBER)


def email_ready() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def imap_ready() -> bool:
    return bool(IMAP_HOST and IMAP_USER and IMAP_PASS)


def get_twilio_client():
    if not sms_ready():
        return None
    try:
        from twilio.rest import Client
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        if SPACE_URL:
            su = SPACE_URL.strip().rstrip("/")
            if not su.startswith("http"):
                su = f"https://{su}"
            client.api.base_url = su
        return client
    except Exception as e:
        logger.error("Twilio client init failed: %s", e)
        return None


# ── Task: follow up on new leads by SMS ───────────────────────────────────────
async def task_follow_up_leads() -> int:
    """
    Find leads that submitted N+ hours ago and haven't received a follow-up
    yet, and send them a personalized follow-up text from Sam.
    Tracks follow-up count in message_events to avoid over-messaging.
    """
    if not sms_ready():
        logger.info("SMS not configured — skipping lead follow-up task.")
        return 0

    conn = get_db()
    cutoff = datetime.utcnow() - timedelta(hours=FOLLOW_UP_HOURS)

    # Leads created more than FOLLOW_UP_HOURS ago
    leads = conn.execute(
        """
        SELECT l.id, l.full_name, l.phone, l.requested_product, l.application_type,
               l.requested_amount, l.status, l.created_at,
               COUNT(me.id) AS follow_up_count
        FROM leads l
        LEFT JOIN message_events me
            ON me.lead_id = l.id
            AND me.direction = 'outbound'
            AND me.body LIKE '%follow%'
        WHERE l.status NOT IN ('closed', 'converted', 'unsubscribed')
          AND l.phone IS NOT NULL AND l.phone != ''
          AND datetime(l.created_at) <= ?
        GROUP BY l.id
        HAVING follow_up_count < ?
        LIMIT 50
        """,
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"), MAX_FOLLOW_UPS),
    ).fetchall()

    if not leads:
        logger.info("No leads need follow-up right now.")
        conn.close()
        return 0

    client = get_twilio_client()
    sent = 0

    for lead in leads:
        first_name   = (lead["full_name"] or "there").split()[0]
        product      = lead["requested_product"] or lead["application_type"] or "financing"
        amount_str   = f" (${lead['requested_amount']:,.0f})" if lead["requested_amount"] else ""
        apply_link   = f"{PUBLIC_BASE_URL}/apply" if PUBLIC_BASE_URL else "your application link"

        # AI-generated or template follow-up message
        msg = await _craft_follow_up_sms(first_name, product, amount_str, lead["follow_up_count"])

        try:
            client.messages.create(body=msg, from_=FROM_NUMBER, to=lead["phone"])
            conn.execute(
                "INSERT INTO message_events (lead_id, direction, channel, to_number, body) VALUES (?,?,?,?,?)",
                (lead["id"], "outbound", "sms", lead["phone"], msg),
            )
            conn.commit()
            logger.info("Follow-up SMS sent to lead #%s (%s)", lead["id"], lead["phone"])
            sent += 1
        except Exception as e:
            logger.warning("Follow-up SMS failed for lead #%s: %s", lead["id"], e)

    conn.close()
    return sent


async def _craft_follow_up_sms(
    first_name: str, product: str, amount_str: str, follow_up_num: int
) -> str:
    """Generate a follow-up SMS — AI if available, template fallback."""
    apply_url = f"{PUBLIC_BASE_URL}/apply" if PUBLIC_BASE_URL else ""

    if OPENAI_KEY:
        import httpx
        prompt = (
            f"Write a SHORT, friendly follow-up text message (1-2 sentences max) from Sam at "
            f"BizStack Perks to {first_name}, who inquired about {product}{amount_str}. "
            f"This is follow-up #{follow_up_num + 1}. Be warm but not pushy. "
            f"Invite them to reply with questions. "
            f"{'Mention they can apply at ' + apply_url if apply_url and follow_up_num == 0 else ''} "
            f"End with 'Reply STOP to opt out.' Return only the message text."
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 80,
                        "temperature": 0.7,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("AI follow-up SMS failed, using template: %s", e)

    # Template fallback
    if follow_up_num == 0:
        return (
            f"Hi {first_name}! Just checking in — did you get a chance to look over your "
            f"{product} request{amount_str}? Reply here with any questions. Reply STOP to opt out."
        )
    return (
        f"Hi {first_name}, Sam here from BizStack Perks. Still happy to help with your "
        f"{product} question{amount_str}. Just reply! Reply STOP to opt out."
    )


# ── Task: pipeline reminders for pending drafts ────────────────────────────────
async def task_pipeline_reminders() -> int:
    """
    Log a reminder (to the admin) if pipeline drafts have been pending review
    for more than 4 hours. This doesn't send anything externally — just logs
    so the admin dashboard shows the item is stale.
    """
    conn = get_db()
    stale_cutoff = datetime.utcnow() - timedelta(hours=4)

    stale = conn.execute(
        """
        SELECT id, client_name, client_email, product_type, created_at
        FROM intake_pipeline
        WHERE status = 'pending'
          AND datetime(created_at) <= ?
        """,
        (stale_cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    if stale:
        logger.warning(
            "⚠ %d pipeline draft(s) have been pending review for 4+ hours: %s",
            len(stale),
            [f"#{r['id']} ({r['client_name']})" for r in stale],
        )
    else:
        logger.info("Pipeline: no stale drafts.")

    conn.close()
    return len(stale)


# ── Task: poll inbound email inbox ────────────────────────────────────────────
async def task_poll_email() -> int:
    """Poll IMAP inbox and process any new messages."""
    if not imap_ready():
        logger.info("IMAP not configured — skipping email poll.")
        return 0

    from inbound_email import poll_imap_inbox
    conn = get_db()
    try:
        count = await asyncio.to_thread(lambda: poll_imap_inbox(conn))
        logger.info("Email poll: processed %d messages.", count)
        return count
    except Exception as e:
        logger.error("Email poll error: %s", e)
        return 0
    finally:
        conn.close()


# ── Task: outbound proactive call ─────────────────────────────────────────────
async def task_outbound_calls() -> int:
    """
    Make outbound calls to high-priority leads that haven't been reached yet.
    Targets: leads marked 'new', no prior call attempt, created > 1 hour ago.
    """
    if not sms_ready():
        logger.info("Twilio not configured — skipping outbound calls.")
        return 0

    conn  = get_db()
    one_hr_ago = datetime.utcnow() - timedelta(hours=1)

    leads = conn.execute(
        """
        SELECT l.id, l.full_name, l.phone, l.requested_product, l.application_type
        FROM leads l
        LEFT JOIN call_events ce ON ce.to_number = l.phone
        WHERE l.status = 'new'
          AND l.phone IS NOT NULL AND l.phone != ''
          AND ce.call_sid IS NULL
          AND datetime(l.created_at) <= ?
        ORDER BY l.created_at DESC
        LIMIT 10
        """,
        (one_hr_ago.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    if not leads:
        logger.info("Outbound calls: no new leads to call right now.")
        conn.close()
        return 0

    client = get_twilio_client()
    called = 0

    for lead in leads:
        first_name = (lead["full_name"] or "there").split()[0]
        product    = lead["requested_product"] or lead["application_type"] or "financing"
        greet_msg  = (
            f"Hi {first_name}, this is Sam calling from BizStack Perks regarding your "
            f"{product} inquiry. We'd love to connect and help you explore your options. "
            f"Please call us back or reply to our text. Have a great day!"
        )
        voice = os.getenv("TWILIO_VOICE", "Polly.Joanna-Neural")
        callback_url = f"{PUBLIC_BASE_URL}/twilio/voice/status" if PUBLIC_BASE_URL else None

        try:
            twiml = (
                f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response><Say voice="{voice}" language="en-US">{greet_msg}</Say></Response>'
            )
            call_params = {
                "to": lead["phone"],
                "from_": FROM_NUMBER,
                "twiml": twiml,
            }
            if callback_url:
                call_params["status_callback"] = callback_url

            call = client.calls.create(**call_params)
            conn.execute(
                """
                INSERT OR IGNORE INTO call_events
                    (call_sid, direction, call_status, from_number, to_number, message)
                VALUES (?,?,?,?,?,?)
                """,
                (call.sid, "outbound", "initiated", FROM_NUMBER, lead["phone"], greet_msg),
            )
            conn.commit()
            logger.info("Outbound call initiated to lead #%s (%s): %s", lead["id"], lead["phone"], call.sid)
            called += 1
        except Exception as e:
            logger.warning("Outbound call failed for lead #%s: %s", lead["id"], e)

    conn.close()
    return called


async def task_scan_signals() -> int:
    """Find and persist public business-growth signals for configured markets."""
    from business_signals import run_autonomous_signal_scan

    return await run_autonomous_signal_scan(get_db)


# ── Task: status summary ───────────────────────────────────────────────────────
async def task_status_summary() -> None:
    """Print a summary of the current system state."""
    conn = get_db()

    def q(sql, *args):
        try:
            return conn.execute(sql, args).fetchone()[0]
        except Exception:
            return "?"

    print("\n" + "=" * 55)
    print("  BizStack Perks — Bot Agent Status Summary")
    print("=" * 55)
    print(f"  Database:          {DATABASE_PATH}")
    print(f"  SMS (Twilio):      {'✓ configured' if sms_ready() else '✗ not configured'}")
    print(f"  Email (SMTP):      {'✓ configured' if email_ready() else '✗ not configured'}")
    print(f"  Email (IMAP):      {'✓ configured' if imap_ready() else '✗ not configured'}")
    print(f"  OpenAI:            {'✓ configured' if OPENAI_KEY else '✗ not configured'}")
    print(f"  Auto-send emails:  {'ON' if AUTO_SEND_EMAIL else 'OFF (admin review mode)'}")
    print("-" * 55)
    sql_new_leads      = "SELECT COUNT(*) FROM leads WHERE status='new'"
    sql_pipe_pending   = "SELECT COUNT(*) FROM intake_pipeline WHERE status='pending'"
    sql_pipe_sent      = "SELECT COUNT(*) FROM intake_pipeline WHERE status='sent'"
    print(f"  Total leads:       {q('SELECT COUNT(*) FROM leads')}")
    print(f"  New leads:         {q(sql_new_leads)}")
    print(f"  Pipeline pending:  {q(sql_pipe_pending)}")
    print(f"  Pipeline sent:     {q(sql_pipe_sent)}")
    print(f"  Inbound emails:    {q('SELECT COUNT(*) FROM inbound_emails')}")
    print(f"  SMS events:        {q('SELECT COUNT(*) FROM message_events')}")
    print(f"  Call events:       {q('SELECT COUNT(*) FROM call_events')}")
    print("=" * 55 + "\n")
    conn.close()


# ── Run-once mode ─────────────────────────────────────────────────────────────
async def run_once(task_name: Optional[str] = None) -> None:
    """Run all scheduled tasks once (or just the named task), then exit."""
    tasks = {
        "follow-up-leads":    task_follow_up_leads,
        "poll-email":         task_poll_email,
        "pipeline-reminders": task_pipeline_reminders,
        "outbound-calls":     task_outbound_calls,
        "scan-signals":       task_scan_signals,
        "status":             task_status_summary,
    }

    if task_name:
        if task_name not in tasks:
            logger.error("Unknown task '%s'. Available: %s", task_name, list(tasks.keys()))
            return
        logger.info("Running task: %s", task_name)
        await tasks[task_name]()
        return

    logger.info("Running all scheduled tasks (run-once mode)…")
    await task_follow_up_leads()
    await task_poll_email()
    await task_pipeline_reminders()
    await task_scan_signals()
    logger.info("Run-once complete.")


# ── Daemon mode ────────────────────────────────────────────────────────────────
async def run_daemon() -> None:
    """
    Continuous daemon: runs all tasks on their own schedules indefinitely.

    Schedule:
      Every 5 min:  poll email, lead follow-ups
      Every 15 min: pipeline reminders, outbound calls
      Every hour:   status summary
    """
    logger.info("Bot agent daemon started.")
    await task_status_summary()

    tick = 0
    while True:
        try:
            # Every 5 minutes
            if tick % 1 == 0:
                await task_poll_email()
                await task_follow_up_leads()

            # Every 15 minutes (every 3rd tick)
            if tick % 3 == 0:
                await task_pipeline_reminders()
                await task_outbound_calls()

            # Every hour (every 12th tick)
            if tick % 12 == 0:
                await task_status_summary()
                await task_scan_signals()

        except Exception as e:
            logger.error("Daemon task error (continuing): %s", e)

        tick += 1
        await asyncio.sleep(300)  # 5-minute tick


# ── Entry point ────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="BizStack Perks autonomous bot agent")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-once", action="store_true", help="Run all tasks once and exit")
    group.add_argument("--daemon",   action="store_true", help="Run as continuous background daemon")
    group.add_argument("--status",   action="store_true", help="Print system status summary and exit")
    parser.add_argument("--task", help="Run a specific task: follow-up-leads, poll-email, pipeline-reminders, outbound-calls")
    args = parser.parse_args()

    if args.status:
        asyncio.run(task_status_summary())
    elif args.daemon:
        asyncio.run(run_daemon())
    else:
        asyncio.run(run_once(args.task))


if __name__ == "__main__":
    main()
