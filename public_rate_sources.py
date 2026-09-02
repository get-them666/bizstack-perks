"""Live public bank-rate discovery and an optional official-page watch list."""

from datetime import datetime, timezone
import re
from typing import Optional
from urllib.parse import urlparse

from business_signals import YouComSignalScanner

RATE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2}(?:\.\d{1,3})?)\s*%\s*(APR|APY)\b", re.IGNORECASE
)
PUBLIC_EMAIL_PATTERN = re.compile(
    r"\b(?:info|contact|hello|sales|support|office)@[a-z0-9.-]+\.[a-z]{2,}\b",
    re.IGNORECASE,
)
REGION_SEARCH_NAMES = {"VA": "Virginia"}
NON_BANK_SOURCE_DOMAINS = {
    "bankrate.com",
    "cnbc.com",
    "forbes.com",
    "getholdings.com",
    "lendingtree.com",
    "lloydsbank.com",
    "monitorbankrates.com",
    "nerdwallet.com",
    "smartasset.com",
    "usmilitary.org",
    "usnews.com",
    "valoannetwork.com",
}


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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_public_bank_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            region TEXT,
            source_url TEXT UNIQUE NOT NULL,
            source_summary TEXT NOT NULL,
            observed_rate REAL,
            rate_kind TEXT,
            discovered_at TIMESTAMP NOT NULL
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


def _rate_from_text(text: str):
    match = RATE_PATTERN.search(text)
    if not match:
        return None, None
    rate = float(match.group(1))
    if not 0 < rate < 100:
        return None, None
    return rate, (match.group(2) or "rate").upper()


def _is_bank_rate_result(result: dict, product_name: str) -> bool:
    title = str(result.get("title", "")).lower()
    domain = urlparse(str(result.get("url", ""))).netloc.removeprefix("www.").lower()
    text = " ".join(
        str(result.get(field, "")) for field in ("title", "description", "snippets")
    ).lower()
    product_terms = [term for term in product_name.lower().split() if len(term) > 2]
    return (
        bool(result.get("url") and result.get("title"))
        and domain not in NON_BANK_SOURCE_DOMAINS
        and "rate" in text
        and all(term in text for term in product_terms)
        and any(
            term in title or term in domain
            for term in ("bank", "credit union", "creditunion", "cu.org")
        )
    )


async def discover_live_public_bank_rates(
    product_name: str, region: str, limit: int = 35
) -> list[dict]:
    """Discover current public bank-rate pages; do not infer values not displayed."""
    search_region = REGION_SEARCH_NAMES.get(region.upper(), region)
    queries = (
        f"{search_region} {product_name} bank rates",
        f"{search_region} community bank {product_name} rates",
        f"{search_region} credit union {product_name} rates",
    )
    discovered = []
    seen_urls = set()
    scanner = YouComSignalScanner()
    for query in queries:
        results = await scanner.search_current_web(query, 100)
        for result in results.get("web", []) + results.get("news", []):
            if (
                not _is_bank_rate_result(result, product_name)
                or result["url"] in seen_urls
            ):
                continue
            seen_urls.add(result["url"])
            summary = result.get("description") or result.get("title", "")
            rate, rate_kind = _rate_from_text(
                " ".join(
                    str(result.get(field, ""))
                    for field in ("title", "description", "snippets")
                )
            )
            domain = urlparse(result["url"]).netloc.removeprefix("www.")
            discovered.append(
                {
                    "bank_name": result.get("title", "")[:160],
                    "product_name": product_name,
                    "region": region.upper(),
                    "source_url": result["url"],
                    "source_summary": summary[:1000],
                    "observed_rate": rate,
                    "rate_kind": rate_kind,
                    "source_domain": domain,
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(discovered) == limit:
                break
        if len(discovered) == limit:
            break
    return sorted(
        discovered,
        key=lambda result: (
            result["observed_rate"] is None,
            result["observed_rate"] if result["observed_rate"] is not None else 0,
        ),
    )


def store_live_public_bank_rates(conn, rates: list[dict]) -> int:
    inserted = 0
    for rate in rates:
        cursor = conn.execute(
            """
            INSERT INTO live_public_bank_rates (
                bank_name, product_name, region, source_url, source_summary,
                observed_rate, rate_kind, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                bank_name=excluded.bank_name,
                product_name=excluded.product_name,
                region=excluded.region,
                source_summary=excluded.source_summary,
                observed_rate=excluded.observed_rate,
                rate_kind=excluded.rate_kind,
                discovered_at=excluded.discovered_at
            """,
            (
                rate["bank_name"],
                rate["product_name"],
                rate["region"],
                rate["source_url"],
                rate["source_summary"],
                rate["observed_rate"],
                rate["rate_kind"],
                rate["discovered_at"],
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


async def discover_public_business_contact(
    business_name: str, location: str
) -> Optional[str]:
    """Return a publicly displayed generic business contact, never a guessed address."""
    query = f'"{business_name}" "{location}" contact email'
    results = await YouComSignalScanner().search_current_web(query, 20)
    for result in results.get("web", []):
        text = " ".join(
            str(result.get(field, "")) for field in ("title", "description", "snippets")
        )
        match = PUBLIC_EMAIL_PATTERN.search(text)
        if match:
            return match.group(0).lower()
    return None
