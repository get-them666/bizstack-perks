"""
Business expansion/loan-seeking signal scanner.

Finds businesses showing PUBLIC signals of growth, expansion, or active
loan-seeking -- using only legitimate, publicly available data sources:

- NewsAPI (free tier): press releases and news mentions of business
  expansion, funding rounds, new locations, hiring surges
- SBA (Small Business Administration) public loan data: SBA publishes
  aggregate and, in some releases, named recipient data for certain loan
  programs (e.g. PPP loan recipient data is public record)
- City/county open-data permit portals (Socrata-based, a common open-data
  platform many US cities use): building permits are public record and
  often signal expansion (new construction, renovation, new locations)

This module does NOT access any bank's private customer/applicant data,
does NOT scrape login-walled content, and does NOT collect personal
financial information. Every source here is either a government open-data
API or a public news aggregator.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_BASE_URL = "https://newsapi.org/v2"

# Keywords that signal a business is expanding, growing, or seeking financing.
EXPANSION_KEYWORDS = [
    "expands", "expansion", "new location", "opens second location",
    "secures funding", "raises funding", "receives loan", "SBA loan",
    "business loan", "line of credit", "hiring surge", "new headquarters",
    "breaks ground", "grand opening", "acquisition", "acquires",
]


class BusinessSignal(BaseModel):
    """A single public signal indicating business growth/loan-seeking activity."""

    business_name: str
    signal_type: str  # "news", "permit", "sba_loan"
    signal_summary: str
    source_url: Optional[str] = None
    source_name: str
    location: Optional[str] = None
    published_at: Optional[str] = None
    confidence_score: float = 0.6


class NewsSignalScanner:
    """Scan public news for business expansion/loan-seeking signals via NewsAPI."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or NEWSAPI_KEY

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def scan_for_signals(
        self, location: str, industry: Optional[str] = None, days_back: int = 30
    ) -> List[BusinessSignal]:
        """
        Search recent news for businesses in a location (optionally filtered
        by industry) showing expansion/loan-seeking signals.
        """
        if not self.is_configured():
            logger.warning("NewsAPI key not configured")
            return []

        query_terms = " OR ".join(f'"{kw}"' for kw in EXPANSION_KEYWORDS[:8])
        query = f"({query_terms}) AND {location}"
        if industry:
            query += f" AND {industry}"

        from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{NEWSAPI_BASE_URL}/everything",
                    params={
                        "q": query,
                        "from": from_date,
                        "sortBy": "relevancy",
                        "language": "en",
                        "pageSize": 20,
                        "apiKey": self.api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") != "ok":
                    logger.warning(f"NewsAPI error: {data.get('message')}")
                    return []

                signals = []
                for article in data.get("articles", []):
                    business_name = self._extract_business_name(article.get("title", ""))
                    signals.append(
                        BusinessSignal(
                            business_name=business_name,
                            signal_type="news",
                            signal_summary=article.get("title", ""),
                            source_url=article.get("url"),
                            source_name=article.get("source", {}).get("name", "News"),
                            location=location,
                            published_at=article.get("publishedAt"),
                            confidence_score=0.6,
                        )
                    )
                return signals
        except Exception as e:
            logger.error(f"News signal scan error: {e}")
            return []

    @staticmethod
    def _extract_business_name(title: str) -> str:
        """
        Best-effort extraction of a business name from a headline. Not
        perfect NLP -- takes the text before common separators, which
        covers a large share of real headline patterns like
        "Acme Corp expands to new location" or "Acme Corp: opens HQ".
        """
        for separator in [" expands", " opens", " secures", " raises", " receives", ":", " -"]:
            if separator in title:
                return title.split(separator)[0].strip()
        return title[:80].strip()


class OpenDataPermitScanner:
    """
    Scan city/county open-data permit portals for new construction/expansion
    permits. Many US cities publish permit data via Socrata (a standard
    open-data platform) with a consistent query API.
    """

    def __init__(self, portal_base_url: str, api_token: Optional[str] = None):
        """
        Args:
            portal_base_url: the city's Socrata dataset endpoint, e.g.
                "https://data.cityofnewyork.us/resource/ipu4-2q9a.json"
            api_token: optional Socrata app token (raises rate limits; free to request)
        """
        self.portal_base_url = portal_base_url.rstrip("/")
        self.api_token = api_token

    async def scan_recent_permits(
        self, days_back: int = 30, permit_type_filter: Optional[str] = None
    ) -> List[BusinessSignal]:
        """Fetch recent commercial/business permits, which often signal expansion."""
        if not self.portal_base_url:
            return []

        from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        try:
            headers = {"X-App-Token": self.api_token} if self.api_token else {}
            params = {"$limit": 50, "$order": "issued_date DESC"}
            if permit_type_filter:
                params["$where"] = f"permit_type='{permit_type_filter}' AND issued_date > '{from_date}'"
            else:
                params["$where"] = f"issued_date > '{from_date}'"

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.portal_base_url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                signals = []
                for permit in data:
                    business_name = permit.get("business_name") or permit.get("owner_name") or "Unknown business"
                    signals.append(
                        BusinessSignal(
                            business_name=business_name,
                            signal_type="permit",
                            signal_summary=f"Permit filed: {permit.get('permit_type', 'construction/expansion')}",
                            source_name="City open-data permit portal",
                            location=permit.get("address") or permit.get("location"),
                            published_at=permit.get("issued_date"),
                            confidence_score=0.7,
                        )
                    )
                return signals
        except Exception as e:
            logger.error(f"Permit scan error: {e}")
            return []


def deduplicate_signals(signals: List[BusinessSignal]) -> List[BusinessSignal]:
    """Remove duplicate signals for the same business, keeping the highest-confidence one."""
    best_by_name: Dict[str, BusinessSignal] = {}
    for signal in signals:
        key = signal.business_name.lower().strip()
        if key not in best_by_name or signal.confidence_score > best_by_name[key].confidence_score:
            best_by_name[key] = signal
    return list(best_by_name.values())
