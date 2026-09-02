"""Live public bank-rate discovery and an optional official-page watch list."""

import asyncio
from datetime import datetime, timezone
from html import unescape
import re
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

FDIC_INSTITUTIONS_URL = "https://api.fdic.gov/banks/institutions"
RATE_PATTERN = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d{1,3})?)\s*%\s*(APR|APY)\b", re.IGNORECASE)
HREF_PATTERN = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
PUBLIC_EMAIL_PATTERN = re.compile(
    r"\b(?:info|contact|hello|sales|support|office)@[a-z0-9.-]+\.[a-z]{2,}\b",
    re.IGNORECASE,
)
BOT_USER_AGENT = "BizStackPerksRateMonitor/1.0 (+https://bizstackperks.com)"


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


def _rate_for_product(text: str, product_name: str):
    """Return an APR/APY only when the requested product is nearby on the page."""
    product_terms = [term.lower() for term in product_name.split() if len(term) > 2]
    lowered = text.lower()
    for match in RATE_PATTERN.finditer(text):
        rate = float(match.group(1))
        if not 0 < rate < 100:
            continue
        sentence_start = max(
            lowered.rfind(".", 0, match.start()),
            lowered.rfind("!", 0, match.start()),
            lowered.rfind("?", 0, match.start()),
            lowered.rfind("\n", 0, match.start()),
        ) + 1
        sentence_end_candidates = [
            position
            for position in (
                lowered.find(".", match.end()),
                lowered.find("!", match.end()),
                lowered.find("?", match.end()),
                lowered.find("\n", match.end()),
            )
            if position != -1
        ]
        sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(lowered)
        context = lowered[sentence_start:sentence_end]
        if all(term in context for term in product_terms):
            return rate, match.group(2).upper()
    return None, None


async def discover_live_public_bank_rates(
    product_name: str, region: str, limit: int = 35
) -> list[dict]:
    """Inspect public pages for FDIC-listed institutions without guessing rates."""
    banks = await _fdic_banks(region, limit * 2)
    semaphore = asyncio.Semaphore(12)
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": BOT_USER_AGENT},
        timeout=15.0,
    ) as client:
        candidates = await asyncio.gather(
            *(
                _inspect_bank_rate_page(client, semaphore, bank, product_name, region)
                for bank in banks
            )
        )
    discovered = [candidate for candidate in candidates if candidate]
    return sorted(
        discovered[:limit],
        key=lambda result: (
            result["observed_rate"] is None,
            result["observed_rate"] if result["observed_rate"] is not None else 0,
        ),
    )


async def _fdic_banks(region: str, limit: int) -> list[dict]:
    """Get real insured institutions and their published websites from FDIC."""
    banks = []
    seen_domains = set()
    async with httpx.AsyncClient(timeout=15.0) as client:
        offset = 0
        page_size = 100
        while len(banks) < limit:
            response = await client.get(
                FDIC_INSTITUTIONS_URL,
                params={
                    "filters": f"STALP:{region.upper()}",
                    "fields": "NAME,WEBADDR,CERT",
                    "limit": page_size,
                    "offset": offset,
                    "format": "json",
                },
            )
            response.raise_for_status()
            page = response.json().get("data", [])
            if not page:
                break
            for item in page:
                bank = item.get("data", {})
                web_address = str(bank.get("WEBADDR") or "").strip()
                if not web_address:
                    continue
                base_url = (
                    web_address
                    if web_address.startswith("https://")
                    else f"https://{web_address}"
                )
                domain = urlparse(base_url).netloc.lower()
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                banks.append(
                    {"name": bank.get("NAME", "FDIC-listed institution"), "url": base_url}
                )
                if len(banks) == limit:
                    break
            offset += page_size
    return banks


async def _allows_monitoring(client: httpx.AsyncClient, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = await client.get(robots_url)
    except httpx.HTTPError:
        return False
    if response.status_code == 404:
        return True
    if response.status_code != 200:
        return False
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(BOT_USER_AGENT, url)


def _page_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAG_PATTERN.sub(" ", html))).strip()


def _rate_page_links(html: str, base_url: str) -> list[str]:
    links = []
    base_domain = urlparse(base_url).netloc
    for href in HREF_PATTERN.findall(html):
        candidate = urljoin(base_url, unescape(href))
        parsed = urlparse(candidate)
        if (
            parsed.scheme == "https"
            and parsed.netloc == base_domain
            and any(term in candidate.lower() for term in ("rate", "loan", "business", "credit"))
            and candidate not in links
        ):
            links.append(candidate)
    return links[:8]


async def _inspect_bank_rate_page(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    bank: dict,
    product_name: str,
    region: str,
) -> Optional[dict]:
    """Read one FDIC-listed bank's permitted public page and likely rate link."""
    async with semaphore:
        if not await _allows_monitoring(client, bank["url"]):
            return None
        try:
            home = await client.get(bank["url"])
            home.raise_for_status()
        except httpx.HTTPError:
            return None
        pages = [str(home.url)] + _rate_page_links(home.text, str(home.url))
        for page_url in pages:
            if not await _allows_monitoring(client, page_url):
                continue
            try:
                page = home if page_url == str(home.url) else await client.get(page_url)
                page.raise_for_status()
            except httpx.HTTPError:
                continue
            text = _page_text(page.text)
            if not all(term in text.lower() for term in product_name.lower().split()):
                continue
            rate, rate_kind = _rate_for_product(text, product_name)
            return {
                "bank_name": bank["name"][:160],
                "product_name": product_name,
                "region": region.upper(),
                "source_url": str(page.url),
                "source_summary": (
                    f"FDIC-listed institution public page retrieved at "
                    f"{datetime.now(timezone.utc).isoformat()}"
                ),
                "observed_rate": rate,
                "rate_kind": rate_kind,
                "source_domain": urlparse(str(page.url)).netloc.removeprefix("www."),
                "discovered_at": datetime.now(timezone.utc).isoformat(),
            }
    return None


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
    from business_signals import YouComSignalScanner

    results = await YouComSignalScanner().search_current_web(query, 20)
    for result in results.get("web", []):
        text = " ".join(
            str(result.get(field, "")) for field in ("title", "description", "snippets")
        )
        match = PUBLIC_EMAIL_PATTERN.search(text)
        if match:
            return match.group(0).lower()
    return None
