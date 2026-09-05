"""
Business expansion/loan-seeking signal scanner.

Finds businesses showing PUBLIC signals of growth, expansion, or active
loan-seeking -- using only legitimate, publicly available data sources:

- You.com's free live-search MCP service: current web and news mentions of
  expansions, new locations, hiring surges, and funding activity.

This module does NOT access any bank's private customer/applicant data,
does NOT scrape login-walled content, and does NOT collect personal
financial information. Every source here is either a government open-data
API or a public news aggregator.
"""

import os
import logging
import json
from typing import Callable, Optional, List, Dict, Any
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

YOUCOM_MCP_URL = "https://api.you.com/mcp?profile=free"
MCP_PROTOCOL_VERSION = "2025-03-26"

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


class YouComSignalScanner:
    """Scan current US news through You.com's free live-search MCP profile."""

    @staticmethod
    def _parse_mcp_response(body: str) -> Dict[str, Any]:
        """Extract the JSON-RPC result from a Streamable HTTP SSE response."""
        for line in body.splitlines():
            if not line.startswith("data: "):
                continue
            message = json.loads(line[6:])
            if "error" in message:
                raise RuntimeError(
                    f"You.com live search failed: {message['error'].get('message', 'unknown error')}"
                )
            if "result" in message:
                return message["result"]
        raise RuntimeError("You.com live search returned no result")

    @staticmethod
    def _freshness(days_back: int) -> str:
        if days_back <= 1:
            return "day"
        if days_back <= 7:
            return "week"
        return "month"

    @staticmethod
    def _signals_from_results(
        results: Dict[str, Any], location: str
    ) -> List[BusinessSignal]:
        articles = [
            (article, "You.com live news")
            for article in results.get("news", [])
        ] + [
            (article, "You.com live web")
            for article in results.get("web", [])
        ]
        location_terms = [term.strip().lower() for term in location.split(",") if term.strip()]
        signals = []
        for article, source_name in articles:
            title = article.get("title", "")
            url = article.get("url")
            article_text = " ".join(
                str(article.get(field, ""))
                for field in ("title", "description", "snippets")
            ).lower()
            if (
                not title
                or not url
                or not all(term in article_text for term in location_terms)
                or not any(keyword in article_text for keyword in EXPANSION_KEYWORDS)
            ):
                continue
            signals.append(
                BusinessSignal(
                    business_name=_extract_business_name(title),
                    signal_type="news",
                    signal_summary=title,
                    source_url=url,
                    source_name=source_name,
                    location=location,
                    published_at=article.get("page_age"),
                    confidence_score=0.75,
                )
            )
        return signals

    async def _call(
        self,
        client: httpx.AsyncClient,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "BizStackPerksSignalBot/1.0",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        try:
            response = await client.post(YOUCOM_MCP_URL, headers=headers, json=payload)
            response.raise_for_status()
            return (
                self._parse_mcp_response(response.text),
                response.headers.get("mcp-session-id") or session_id,
            )
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"You.com live search failed: {error}") from error

    async def search_current_web(self, query: str, count: int = 20) -> Dict[str, Any]:
        """Return only the results obtained during this live You.com search."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            initialized, session_id = await self._call(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "bizstack-perks", "version": "1.0"},
                    },
                },
            )
            if initialized.get("protocolVersion") != MCP_PROTOCOL_VERSION:
                raise RuntimeError("You.com live search returned an unsupported MCP protocol")
            search_result, _ = await self._call(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "you-search",
                        "arguments": {
                            "query": query,
                            "freshness": "month",
                            "country": "US",
                            "count": min(max(count, 1), 100),
                        },
                    },
                },
                session_id,
            )
        content = search_result.get("content", [])
        if not content or not isinstance(content[0].get("text"), str):
            raise RuntimeError("You.com live search returned an invalid result")
        try:
            return json.loads(content[0]["text"]).get("results", {})
        except json.JSONDecodeError as error:
            raise RuntimeError("You.com live search returned malformed result data") from error

    async def scan_for_signals(
        self, location: str, industry: Optional[str] = None, days_back: int = 30
    ) -> List[BusinessSignal]:
        query = f'"{location}" business expansion'
        if industry:
            query = f'"{location}" {industry} expansion'

        results = await self.search_current_web(query, 20)
        return self._signals_from_results(results, location)


async def scan_public_signals(
    location: str, industry: Optional[str] = None, days_back: int = 30
) -> List[BusinessSignal]:
    """Return only fresh signals fetched through the configured live provider."""
    return deduplicate_signals(
        await YouComSignalScanner().scan_for_signals(location, industry, days_back)
    )


def init_signal_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS business_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            signal_summary TEXT NOT NULL,
            source_url TEXT UNIQUE NOT NULL,
            source_name TEXT NOT NULL,
            location TEXT,
            published_at TEXT,
            confidence_score REAL NOT NULL,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def store_signals(conn, signals: List[BusinessSignal]) -> int:
    """Persist unique public signals so scans remain available after reload."""
    inserted = 0
    for signal in signals:
        if not signal.source_url:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO business_signals (
                business_name, signal_type, signal_summary, source_url,
                source_name, location, published_at, confidence_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.business_name,
                signal.signal_type,
                signal.signal_summary,
                signal.source_url,
                signal.source_name,
                signal.location,
                signal.published_at,
                signal.confidence_score,
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def signal_scan_targets() -> List[Dict[str, Any]]:
    """Read autonomous scan targets from a JSON environment variable."""
    try:
        targets = json.loads(os.getenv("SIGNAL_SCAN_TARGETS", "[]"))
    except json.JSONDecodeError:
        logger.error("SIGNAL_SCAN_TARGETS must be a JSON array")
        return []
    return [
        target
        for target in targets
        if isinstance(target, dict) and isinstance(target.get("location"), str)
    ]


async def run_autonomous_signal_scan(conn_factory: Callable[[], Any]) -> int:
    """Run one configured discovery sweep and retain the results."""
    total = 0
    for target in signal_scan_targets():
        industries = target.get("industries")
        if not isinstance(industries, list):
            industries = [target.get("industry")]
        for industry in industries:
            if industry is not None and not isinstance(industry, str):
                logger.warning("Ignoring an invalid industry for %s", target["location"])
                continue
            signals = await scan_public_signals(
                target["location"], industry, int(target.get("days_back", 30))
            )
            conn = conn_factory()
            try:
                total += store_signals(conn, signals)
            finally:
                conn.close()
    logger.info("Autonomous signal scan stored %d new signals", total)
    return total


def deduplicate_signals(signals: List[BusinessSignal]) -> List[BusinessSignal]:
    """Remove duplicate signals for the same business, keeping the highest-confidence one."""
    best_by_name: Dict[str, BusinessSignal] = {}
    for signal in signals:
        key = signal.business_name.lower().strip()
        if key not in best_by_name or signal.confidence_score > best_by_name[key].confidence_score:
            best_by_name[key] = signal
    return list(best_by_name.values())
