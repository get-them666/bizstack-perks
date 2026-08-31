"""
Local/regional bank loan rate comparison.

IMPORTANT DESIGN DECISION: This module does NOT scrape individual banks'
websites. Many banks' Terms of Service explicitly prohibit automated
scraping, even of public marketing pages, and that varies bank-by-bank in
ways that can't be verified generically. Instead, this uses a curated,
manually-maintained rate sheet (bank_rates.json) -- the same pattern as
perks.json for affiliates. You (or a human researcher) check each bank's
published rates periodically and update the file; the app then uses that
data to build rate comparisons for outreach emails and write-ups.

This keeps the feature 100% legal and safe while still delivering real,
current rate comparisons. If you later want live-scraped rates, that would
require either (a) each bank's explicit permission/API access, or (b) a
licensed commercial rate-data provider (e.g. Bankrate's commercial API).
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BANK_RATES_PATH = os.path.join(BASE_DIR, "bank_rates.json")


def load_bank_rates() -> List[Dict[str, Any]]:
    """
    Load the curated local bank rate sheet. Expected format:
    [
        {
            "bank_name": "First National Bank",
            "loan_type": "business_term_loan",
            "apr_low": 6.5,
            "apr_high": 11.0,
            "min_loan_amount": 10000,
            "max_loan_amount": 500000,
            "notes": "Requires 2 years in business",
            "source_url": "https://example-bank.com/business-loans",
            "last_verified": "2026-08-01",
            "region": "VA"
        },
        ...
    ]
    """
    try:
        with open(BANK_RATES_PATH, encoding="utf-8") as f:
            rates = json.load(f)
    except FileNotFoundError:
        logger.warning(f"bank_rates.json not found at {BANK_RATES_PATH}")
        return []
    except json.JSONDecodeError:
        logger.warning("bank_rates.json contains invalid JSON")
        return []

    if not isinstance(rates, list):
        logger.warning("bank_rates.json must be a JSON array")
        return []

    return [r for r in rates if isinstance(r, dict) and "bank_name" in r and "apr_low" in r]


def get_best_rates_for_region(
    region: Optional[str] = None, loan_type: Optional[str] = None, limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Get the best (lowest APR) available loan rates for a region/loan type,
    sorted by lowest APR first. If region/loan_type are omitted, returns
    across all entries.
    """
    rates = load_bank_rates()

    filtered = rates
    if region:
        filtered = [r for r in filtered if r.get("region", "").upper() == region.upper()]
    if loan_type:
        filtered = [r for r in filtered if r.get("loan_type") == loan_type]

    return sorted(filtered, key=lambda r: r.get("apr_low", 999))[:limit]


def check_rate_staleness(max_age_days: int = 90) -> List[Dict[str, Any]]:
    """
    Return rate entries that haven't been verified recently, so you know
    which ones need a manual re-check. Since this is a curated (not
    scraped) dataset, staleness tracking matters.
    """
    rates = load_bank_rates()
    stale = []

    for rate in rates:
        last_verified = rate.get("last_verified")
        if not last_verified:
            stale.append({**rate, "staleness_reason": "never verified"})
            continue
        try:
            verified_date = datetime.strptime(last_verified, "%Y-%m-%d")
            age_days = (datetime.now() - verified_date).days
            if age_days > max_age_days:
                stale.append({**rate, "staleness_reason": f"{age_days} days old"})
        except ValueError:
            stale.append({**rate, "staleness_reason": "invalid date format"})

    return stale


def format_rates_for_display(rates: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Turn raw rate entries into a human-friendly list for write-ups/emails."""
    formatted = []
    for rate in rates:
        apr_low = rate.get("apr_low")
        apr_high = rate.get("apr_high")
        apr_range = f"{apr_low}%" if apr_low == apr_high or not apr_high else f"{apr_low}%\u2013{apr_high}%"

        formatted.append({
            "bank_name": rate.get("bank_name", "Unknown"),
            "loan_type": (rate.get("loan_type") or "").replace("_", " ").title(),
            "apr_range": apr_range,
            "loan_range": f"${rate.get('min_loan_amount', 0):,} \u2013 ${rate.get('max_loan_amount', 0):,}"
            if rate.get("min_loan_amount") and rate.get("max_loan_amount")
            else "Contact bank for details",
            "notes": rate.get("notes", ""),
            "source_url": rate.get("source_url", ""),
            "last_verified": rate.get("last_verified", "unknown"),
        })
    return formatted
