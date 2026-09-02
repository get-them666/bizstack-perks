"""Admin-managed watch list of official public bank-rate pages."""

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


def init_public_rate_source_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public_bank_rate_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            region TEXT,
            source_url TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP NOT NULL,
            last_checked_at TIMESTAMP,
            last_check_status TEXT
        )
        """
    )
    conn.commit()


def validate_public_https_url(source_url: str) -> str:
    normalized = source_url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Enter an official HTTPS public rate-page URL.")
    return normalized


def add_public_rate_source(
    conn,
    bank_name: str,
    product_name: str,
    region: Optional[str],
    source_url: str,
) -> None:
    conn.execute(
        """
        INSERT INTO public_bank_rate_sources (
            bank_name, product_name, region, source_url, added_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            bank_name.strip(),
            product_name.strip(),
            region.strip().upper() if region else None,
            validate_public_https_url(source_url),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def list_public_rate_sources(conn):
    return conn.execute(
        """
        SELECT id, bank_name, product_name, region, source_url, added_at,
               last_checked_at, last_check_status
        FROM public_bank_rate_sources
        ORDER BY added_at DESC
        """
    ).fetchall()
