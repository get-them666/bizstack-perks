"""
Customer account management: auto-provisioning on Stripe payment, phone-based
OTP login (reuses existing Twilio SMS infra), and per-customer lead scoping.

This is intentionally separate from the admin auth system (BIZSTACK_ADMIN_USER/PASS).
Customers NEVER get access to /dashboard, /admin, or /client — those remain
owner-only. Customers only ever see their own data through the customer portal.
"""

import os
import secrets
import sqlite3
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# OTP codes are short-lived and single-use.
OTP_TTL_SECONDS = 10 * 60  # 10 minutes
OTP_LENGTH = 6

# In-memory OTP store: {phone: {"code": str, "expires_at": float, "attempts": int}}
# Process-local is fine here since OTPs are short-lived and this rarely needs to
# survive a restart; a real multi-instance deployment would move this to the DB
# or a shared cache, but that's overkill for this app's current scale.
_OTP_STORE: dict[str, dict] = {}
_MAX_OTP_ATTEMPTS = 5


def init_customer_tables(conn: sqlite3.Connection) -> None:
    """Create customer-related tables if they don't exist."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT,
            email TEXT UNIQUE,
            phone TEXT UNIQUE,
            stripe_customer_id TEXT UNIQUE,
            stripe_subscription_id TEXT,
            subscription_status TEXT DEFAULT 'inactive',
            portal_session_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Add customer_id to leads so leads can be scoped to whoever paid for them.
    # SQLite doesn't support "ADD COLUMN IF NOT EXISTS", so check first.
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(leads)").fetchall()}
    if "customer_id" not in existing_cols:
        cursor.execute("ALTER TABLE leads ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
    conn.commit()


def provision_customer_from_checkout(
    conn: sqlite3.Connection,
    *,
    email: Optional[str],
    business_name: Optional[str],
    stripe_customer_id: Optional[str],
    stripe_subscription_id: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[int]:
    """
    Create or update a customer record after a successful Stripe checkout/payment.
    Returns the customer's internal id, or None if there isn't enough info
    (e.g. no email and no phone) to create an account.
    """
    if not email and not phone:
        logger.warning("Cannot provision customer without email or phone")
        return None

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO customers (business_name, email, phone, stripe_customer_id, stripe_subscription_id, subscription_status)
        VALUES (?, ?, ?, ?, ?, 'active')
        ON CONFLICT(email) DO UPDATE SET
            business_name = COALESCE(excluded.business_name, customers.business_name),
            stripe_customer_id = COALESCE(excluded.stripe_customer_id, customers.stripe_customer_id),
            stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, customers.stripe_subscription_id),
            subscription_status = 'active',
            updated_at = CURRENT_TIMESTAMP
        """,
        (business_name, email, phone, stripe_customer_id, stripe_subscription_id),
    )
    conn.commit()

    row = conn.execute("SELECT id FROM customers WHERE email = ?", (email,)).fetchone()
    return row["id"] if row else None


def mark_subscription_status(
    conn: sqlite3.Connection, stripe_customer_id: str, status: str
) -> None:
    """Update a customer's subscription status (e.g. after a cancellation webhook)."""
    conn.execute(
        "UPDATE customers SET subscription_status = ?, updated_at = CURRENT_TIMESTAMP WHERE stripe_customer_id = ?",
        (status, stripe_customer_id),
    )
    conn.commit()


def get_customer_by_id(conn: sqlite3.Connection, customer_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()


def get_customer_by_phone(conn: sqlite3.Connection, phone: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()


def get_customer_by_email(conn: sqlite3.Connection, email: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()


def link_phone_to_customer(conn: sqlite3.Connection, customer_id: int, phone: str) -> None:
    """Attach/update a phone number for OTP login on an existing customer record."""
    conn.execute(
        "UPDATE customers SET phone = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (phone, customer_id),
    )
    conn.commit()


def get_customer_leads(conn: sqlite3.Connection, customer_id: int, limit: int = 200) -> list[sqlite3.Row]:
    """Return only the leads that belong to this customer — never another customer's."""
    return conn.execute(
        """
        SELECT id, full_name, email, phone, application_type, requested_product,
               requested_amount, source, status, created_at
        FROM leads
        WHERE customer_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (customer_id, limit),
    ).fetchall()


# ============================================================================
# Phone-based OTP login (reuses existing Twilio SMS infrastructure)
# ============================================================================


def _prune_expired_otps() -> None:
    now = time.time()
    expired = [phone for phone, data in _OTP_STORE.items() if data["expires_at"] < now]
    for phone in expired:
        _OTP_STORE.pop(phone, None)


def generate_otp(phone: str) -> str:
    """Generate and store a fresh OTP code for a phone number."""
    _prune_expired_otps()
    code = f"{secrets.randbelow(10**OTP_LENGTH):06d}"
    _OTP_STORE[phone] = {
        "code": code,
        "expires_at": time.time() + OTP_TTL_SECONDS,
        "attempts": 0,
    }
    return code


def verify_otp(phone: str, code: str) -> bool:
    """Check a submitted OTP code. Single-use: consumes the code on success."""
    _prune_expired_otps()
    entry = _OTP_STORE.get(phone)
    if not entry:
        return False

    entry["attempts"] += 1
    if entry["attempts"] > _MAX_OTP_ATTEMPTS:
        _OTP_STORE.pop(phone, None)
        return False

    if secrets.compare_digest(entry["code"], code.strip()):
        _OTP_STORE.pop(phone, None)  # single-use
        return True

    return False


def create_portal_session_token(conn: sqlite3.Connection, customer_id: int) -> str:
    """Generate a fresh session token for the customer portal and persist it."""
    token = secrets.token_hex(32)
    conn.execute(
        "UPDATE customers SET portal_session_token = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (token, customer_id),
    )
    conn.commit()
    return token


def get_customer_by_session_token(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    if not token:
        return None
    return conn.execute("SELECT * FROM customers WHERE portal_session_token = ?", (token,)).fetchone()


def clear_portal_session(conn: sqlite3.Connection, customer_id: int) -> None:
    conn.execute(
        "UPDATE customers SET portal_session_token = NULL WHERE id = ?",
        (customer_id,),
    )
    conn.commit()
