"""
Public banking & economic data integration (FRED - Federal Reserve Economic Data)
and a client-ready write-up generator that combines Census demographics with
FRED economic indicators into a professional targeting document.

FRED is the Federal Reserve Bank of St. Louis's free, public economic
database. No account fees, no scraping, no ToS violations -- just an
official government-run API. Get a free key at:
https://fred.stlouisfed.org/docs/api/api_key.html

This module does NOT touch anyone's personal bank account, transaction, or
credit data. It only reads aggregate, publicly published economic series
(interest rates, loan volume trends, delinquency rates, etc.) that the
Federal Reserve itself publishes for public use.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# Useful, well-known public FRED series for small-business/lending context.
# Series IDs are stable identifiers published by the Fed -- see
# https://fred.stlouisfed.org/tags/series for the full public catalog.
FRED_SERIES = {
    "mortgage_30yr": "MORTGAGE30US",           # 30-Year Fixed Rate Mortgage Average
    "fed_funds_rate": "FEDFUNDS",               # Effective Federal Funds Rate
    "prime_rate": "MPRIME",                     # Bank Prime Loan Rate
    "business_loans": "BUSLOANS",               # Commercial & Industrial Loans, All Banks
    "delinquency_rate_business": "DRBLACBS",    # Delinquency Rate on Business Loans
    "consumer_credit": "TOTALSL",                # Total Consumer Credit Outstanding
    "unemployment_rate": "UNRATE",               # National Unemployment Rate
    "small_business_optimism": "USSLIND",        # Leading Index (small biz proxy)
}


class FredEconomicData:
    """Fetch public banking/economic indicators from the Federal Reserve's FRED API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or FRED_API_KEY

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def get_series_latest(self, series_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent observation for a FRED series."""
        if not self.is_configured():
            logger.warning("FRED API key not configured")
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{FRED_BASE_URL}/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": self.api_key,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 1,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                observations = data.get("observations", [])
                if not observations:
                    return None
                obs = observations[0]
                return {
                    "series_id": series_id,
                    "date": obs.get("date"),
                    "value": obs.get("value"),
                }
        except Exception as e:
            logger.error(f"FRED API error for series {series_id}: {e}")
            return None

    async def get_lending_snapshot(self) -> Dict[str, Any]:
        """
        Get a snapshot of current public banking/lending indicators, useful
        context for a targeting write-up (rates, credit conditions, etc.).
        """
        if not self.is_configured():
            return {}

        snapshot = {}
        for label, series_id in FRED_SERIES.items():
            result = await self.get_series_latest(series_id)
            if result:
                snapshot[label] = result

        return snapshot

    async def get_series_trend(self, series_id: str, months: int = 12) -> List[Dict[str, Any]]:
        """Get recent history for a series, useful for showing a trend line."""
        if not self.is_configured():
            return []

        start_date = (datetime.now() - timedelta(days=months * 31)).strftime("%Y-%m-%d")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{FRED_BASE_URL}/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": self.api_key,
                        "file_type": "json",
                        "observation_start": start_date,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    {"date": o["date"], "value": o["value"]}
                    for o in data.get("observations", [])
                    if o.get("value") not in (None, ".", "")
                ]
        except Exception as e:
            logger.error(f"FRED trend error for series {series_id}: {e}")
            return []


def format_lending_snapshot_for_display(snapshot: Dict[str, Any]) -> List[Dict[str, str]]:
    """Turn a raw FRED snapshot dict into a human-friendly list for templates/write-ups."""
    labels = {
        "mortgage_30yr": ("30-Year Mortgage Rate", "%"),
        "fed_funds_rate": ("Federal Funds Rate", "%"),
        "prime_rate": ("Bank Prime Loan Rate", "%"),
        "business_loans": ("Commercial & Industrial Loans (Billions $)", "$B"),
        "delinquency_rate_business": ("Business Loan Delinquency Rate", "%"),
        "consumer_credit": ("Total Consumer Credit Outstanding (Billions $)", "$B"),
        "unemployment_rate": ("National Unemployment Rate", "%"),
        "small_business_optimism": ("Leading Economic Index", "index"),
    }

    formatted = []
    for key, data in snapshot.items():
        label, unit = labels.get(key, (key, ""))
        formatted.append({
            "label": label,
            "value": data.get("value", "N/A"),
            "unit": unit,
            "as_of": data.get("date", "N/A"),
        })
    return formatted
