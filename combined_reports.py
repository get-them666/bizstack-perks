"""
Combined report generator: pulls together output from multiple existing
backend tools into ONE structured document, for two audiences:

- Client Report: market opportunity write-up + discovered business signals
  + a ready-to-send outreach draft, all in one document you can hand to (or
  email) a prospective client.
- Banker Report: bank lending-rate comparison + national lending/economic
  climate, all in one document suited for a banker/lender contact.

This does not duplicate logic from writeup_generator.py, business_signals.py,
local_bank_rates.py, public_rate_sources.py, or outreach_generator.py -- it
calls into them and assembles their output into a single narrative.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from writeup_generator import generate_targeting_writeup
from business_signals import scan_public_signals, BusinessSignal
from outreach_generator import generate_outreach_email
from local_bank_rates import get_best_rates_for_region, format_rates_for_display
from banking_data import FredEconomicData, format_lending_snapshot_for_display

logger = logging.getLogger(__name__)


async def generate_client_report(
    state: str,
    service_category: str,
    census_api_key: str,
    fred_api_key: str,
    county_fips: Optional[str] = None,
    business_name: Optional[str] = None,
    location_for_signals: Optional[str] = None,
    sender_name: Optional[str] = None,
    sender_company: Optional[str] = None,
    sender_physical_address: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One-click "Client Report": combines the market opportunity write-up,
    any discovered business signals for the area, and a ready-to-send
    outreach draft for the strongest signal found -- all in one document.
    """
    sections: List[str] = []
    sections.append(f"CLIENT REPORT — {service_category.title()} Opportunity")
    sections.append("=" * 70)
    sections.append(f"Prepared {datetime.utcnow().strftime('%B %d, %Y')} | BizStack Perks")
    sections.append("")

    # --- Section 1: Market opportunity write-up ---
    writeup = await generate_targeting_writeup(
        state=state,
        service_category=service_category,
        census_api_key=census_api_key,
        fred_api_key=fred_api_key,
        county_fips=county_fips,
        business_name=business_name,
    )
    sections.append("PART 1 — MARKET OPPORTUNITY BRIEF")
    sections.append(writeup["narrative_text"])
    sections.append("")

    # --- Section 2: Discovered business signals ---
    signals: List[BusinessSignal] = []
    signal_location = location_for_signals or writeup["demographic_profile"].get("name") or state
    try:
        signals = await scan_public_signals(location=signal_location, industry=service_category)
    except Exception as e:
        logger.warning(f"Signal scan failed while building client report: {e}")

    sections.append("=" * 70)
    sections.append("PART 2 — DISCOVERED BUSINESS SIGNALS")
    sections.append("-" * 70)
    if signals:
        for signal in signals[:10]:
            sections.append(f"• {signal.business_name}: {signal.signal_summary}")
            sections.append(f"  Source: {signal.source_name}" + (f" — {signal.source_url}" if signal.source_url else ""))
        sections.append("")
        sections.append(f"{len(signals)} signal(s) found. See Part 3 for a ready-to-send outreach draft for the strongest match.")
    else:
        sections.append("No fresh public business signals found for this location/industry right now.")
    sections.append("")

    # --- Section 3: Ready-to-send outreach draft (best signal) ---
    sections.append("=" * 70)
    sections.append("PART 3 — READY-TO-SEND OUTREACH DRAFT")
    sections.append("-" * 70)
    outreach_email = None
    if signals and sender_name and sender_company and sender_physical_address and unsubscribe_url:
        best_signal = max(signals, key=lambda s: s.confidence_score)
        outreach_email = generate_outreach_email(
            signal=best_signal,
            sender_name=sender_name,
            sender_company=sender_company,
            sender_physical_address=sender_physical_address,
            unsubscribe_url=unsubscribe_url,
            region=state,
        )
        sections.append(f"Subject: {outreach_email['subject']}")
        sections.append("")
        sections.append(outreach_email["body"])
    elif signals:
        sections.append(
            "A signal was found, but sender details (name, company, physical address) were "
            "not provided, so a draft could not be generated. Provide those to generate a "
            "ready-to-send email."
        )
    else:
        sections.append("No signal available to draft outreach for.")
    sections.append("")

    sections.append("=" * 70)
    sections.append("Sources: U.S. Census Bureau ACS, Federal Reserve FRED, live public business news search.")
    sections.append(f"Generated {datetime.utcnow().strftime('%B %d, %Y')} by BizStack Perks.")

    full_report_text = "\n".join(sections)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "report_type": "client",
        "state": state,
        "service_category": service_category,
        "writeup": writeup,
        "signals": [s.dict() for s in signals],
        "outreach_email": outreach_email,
        "report_text": full_report_text,
    }


async def generate_banker_report(
    state: str,
    fred_api_key: str,
    loan_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One-click "Banker Report": combines curated local bank rate comparisons
    with the current national lending/economic climate -- suited for a
    banker or lender contact rather than a prospective end-client.
    """
    sections: List[str] = []
    sections.append(f"BANKER REPORT — Lending Climate & Rate Comparison ({state})")
    sections.append("=" * 70)
    sections.append(f"Prepared {datetime.utcnow().strftime('%B %d, %Y')} | BizStack Perks")
    sections.append("")

    # --- Section 1: Curated local bank rates ---
    curated_rates = get_best_rates_for_region(region=state, loan_type=loan_type, limit=10)
    formatted_curated = format_rates_for_display(curated_rates)

    sections.append("PART 1 — LOCAL BANK RATE COMPARISON (curated, manually verified)")
    sections.append("-" * 70)
    if formatted_curated:
        for rate in formatted_curated:
            sections.append(f"• {rate['bank_name']} — {rate['loan_type']}: {rate['apr_range']} APR")
            if rate.get("loan_range") and rate["loan_range"] != "Contact bank for details":
                sections.append(f"  Loan amounts: {rate['loan_range']}")
            sections.append(f"  Last verified: {rate.get('last_verified', 'unknown')}")
    else:
        sections.append(f"No curated rate entries on file for {state} yet. Add entries to bank_rates.json.")
    sections.append("")

    # --- Section 2: National lending & economic climate ---
    fred = FredEconomicData(fred_api_key)
    lending_snapshot = await fred.get_lending_snapshot()
    lending_display = format_lending_snapshot_for_display(lending_snapshot)

    sections.append("=" * 70)
    sections.append("PART 2 — NATIONAL LENDING & ECONOMIC CLIMATE (Federal Reserve FRED)")
    sections.append("-" * 70)
    if lending_display:
        for item in lending_display:
            value = item["value"]
            unit = item["unit"]
            if unit == "%":
                display_value = f"{value}%"
            elif unit == "$B":
                display_value = f"${value} billion"
            elif unit == "$M":
                display_value = f"${value} million"
            elif unit == "index":
                display_value = f"{value} (index)"
            else:
                display_value = value
            sections.append(f"• {item['label']}: {display_value} (as of {item['as_of']})")
    else:
        sections.append("Lending snapshot unavailable (check FRED_API_KEY configuration).")
    sections.append("")

    sections.append("=" * 70)
    sections.append(
        "Data sources: manually-verified local bank rate sheet (bank_rates.json); Federal "
        "Reserve Bank of St. Louis (FRED) public economic database. Curated rates should be "
        "re-verified directly with each institution before relying on them."
    )
    sections.append(f"Generated {datetime.utcnow().strftime('%B %d, %Y')} by BizStack Perks.")

    full_report_text = "\n".join(sections)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "report_type": "banker",
        "state": state,
        "loan_type": loan_type,
        "curated_rates": formatted_curated,
        "lending_snapshot": lending_display,
        "report_text": full_report_text,
    }
