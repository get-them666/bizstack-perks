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
    customer_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(customers)").fetchall()
    }
    if "portal_session_token" not in customer_columns:
        cursor.execute("ALTER TABLE customers ADD COLUMN portal_session_token TEXT")
    if "updated_at" not in customer_columns:
        cursor.execute("ALTER TABLE customers ADD COLUMN updated_at TIMESTAMP")
        cursor.execute(
            "UPDATE customers SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
        )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_otps (
            identifier TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            expires_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
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
    Create or update a customer record after a successful Stripe checkout/payment,
    OR a free self-service signup (see /signup). Returns the customer's internal
    id, or None if there isn't enough info (e.g. no email and no phone) to
    create an account.

    Works correctly whether email, phone, or both are provided -- upserts on
    whichever identifier is present (email takes priority if both are given
    and an existing row matches either).
    """
    if not email and not phone:
        logger.warning("Cannot provision customer without email or phone")
        return None

    cursor = conn.cursor()

    # Look for an existing customer by whichever identifier(s) we have.
    existing = None
    if email:
        existing = conn.execute("SELECT id FROM customers WHERE email = ?", (email,)).fetchone()
    if not existing and phone:
        existing = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()

    if existing:
        customer_id = existing["id"]
        cursor.execute(
            """
            UPDATE customers SET
                business_name = COALESCE(?, business_name),
                email = COALESCE(?, email),
                phone = COALESCE(?, phone),
                stripe_customer_id = COALESCE(?, stripe_customer_id),
                stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                subscription_status = 'active',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (business_name, email, phone, stripe_customer_id, stripe_subscription_id, customer_id),
        )
    else:
        cursor.execute(
            """
            INSERT INTO customers (business_name, email, phone, stripe_customer_id, stripe_subscription_id, subscription_status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (business_name, email, phone, stripe_customer_id, stripe_subscription_id),
        )
        customer_id = cursor.lastrowid

    conn.commit()
    return customer_id


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


def generate_otp(conn: sqlite3.Connection, identifier: str) -> str:
    """Generate and persist a fresh OTP code for a portal login identifier."""
    conn.execute("DELETE FROM portal_otps WHERE expires_at < ?", (time.time(),))
    code = f"{secrets.randbelow(10**OTP_LENGTH):06d}"
    conn.execute(
        """
        INSERT INTO portal_otps (identifier, code, expires_at, attempts)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(identifier) DO UPDATE SET
            code = excluded.code,
            expires_at = excluded.expires_at,
            attempts = 0
        """,
        (identifier, code, time.time() + OTP_TTL_SECONDS),
    )
    conn.commit()
    return code


def verify_otp(conn: sqlite3.Connection, identifier: str, code: str) -> bool:
    """Check a submitted OTP code. Single-use: consumes the code on success."""
    conn.execute("DELETE FROM portal_otps WHERE expires_at < ?", (time.time(),))
    entry = conn.execute(
        "SELECT code, attempts FROM portal_otps WHERE identifier = ?", (identifier,)
    ).fetchone()
    if entry is None:
        conn.commit()
        return False

    attempts = entry["attempts"] + 1
    if attempts > _MAX_OTP_ATTEMPTS:
        conn.execute("DELETE FROM portal_otps WHERE identifier = ?", (identifier,))
        conn.commit()
        return False

    if secrets.compare_digest(entry["code"], code.strip()):
        conn.execute("DELETE FROM portal_otps WHERE identifier = ?", (identifier,))
        conn.commit()
        return True

    conn.execute(
        "UPDATE portal_otps SET attempts = ? WHERE identifier = ?", (attempts, identifier)
    )
    conn.commit()
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
