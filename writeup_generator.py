"""
Client targeting write-up generator: combines Census demographic data with
FRED public banking/economic indicators into a professional, ready-to-send
document for pitching a specific location/service category to a prospect.

This produces a text/HTML write-up you can send by email, text, or read
from on a sales call -- NOT personal financial advice, NOT a scraped or
purchased banking data product. Every figure is sourced from a named
public government dataset (Census ACS or Federal Reserve FRED).
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from lead_sources import CensusLeadAnalyzer
from banking_data import FredEconomicData, format_lending_snapshot_for_display

logger = logging.getLogger(__name__)


async def generate_targeting_writeup(
    state: str,
    service_category: str,
    census_api_key: str,
    fred_api_key: str,
    county_fips: Optional[str] = None,
    business_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a client-ready targeting write-up for a state/county and service
    category, combining:
      - Census demographic profile (population, income, age, housing units)
      - FRED public banking/economic snapshot (rates, lending trends)
      - A generated narrative summary tying the two together

    Returns a dict with both structured data (for an API/JSON response) and
    a formatted plain-text write-up (ready to copy into an email/SMS/call).
    """
    census = CensusLeadAnalyzer(census_api_key)
    fred = FredEconomicData(fred_api_key)

    demographic_profile = await census.get_area_demographic_profile(state, county_fips)
    lending_snapshot = await fred.get_lending_snapshot()
    lending_display = format_lending_snapshot_for_display(lending_snapshot)

    narrative = _build_narrative(
        state=state,
        service_category=service_category,
        demographic_profile=demographic_profile,
        lending_display=lending_display,
        business_name=business_name,
    )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "state": state,
        "county_fips": county_fips,
        "service_category": service_category,
        "demographic_profile": demographic_profile,
        "lending_snapshot": lending_display,
        "narrative_text": narrative,
        "data_sources": [
            "U.S. Census Bureau, American Community Survey (ACS) 5-Year Estimates",
            "Federal Reserve Bank of St. Louis, FRED public economic database",
        ],
    }


def _build_narrative(
    state: str,
    service_category: str,
    demographic_profile: Dict[str, Any],
    lending_display: List[Dict[str, str]],
    business_name: Optional[str] = None,
) -> str:
    """Generate a professional, client-ready narrative from the raw data."""
    lines: List[str] = []

    category_lower = service_category.lower()
    is_financial_category = any(
        term in category_lower for term in ["loan", "credit", "lend", "financ", "mortgage"]
    )

    lines.append(f"MARKET OPPORTUNITY BRIEF — {service_category.title()} in {state}")
    lines.append("=" * 60)
    lines.append("")

    # Demographic section
    if demographic_profile and "counties" not in demographic_profile:
        pop = demographic_profile.get("population")
        income = demographic_profile.get("median_household_income")
        age = demographic_profile.get("median_age")
        housing = demographic_profile.get("housing_units")
        area_name = demographic_profile.get("name", state)

        lines.append(f"AREA: {area_name}")
        lines.append("-" * 60)
        if pop:
            lines.append(f"• Population: {pop:,}")
        if income:
            lines.append(f"• Median household income: ${income:,}")
        if age:
            lines.append(f"• Median age: {age}")
        if housing:
            lines.append(f"• Housing units: {housing:,}")
        lines.append("")

        # Simple opportunity framing based on income + population
        if income and pop:
            if is_financial_category:
                if income < 65000:
                    affordability_note = (
                        "This area's household income sits below the national median, which often "
                        f"means demand for accessible {service_category} with clear terms and fast "
                        "approval tends to outweigh demand for premium, high-minimum products."
                    )
                else:
                    affordability_note = (
                        "This area's household income is above-average, suggesting prospects here can "
                        f"qualify for a wider range of {service_category} products, including larger "
                        "loan sizes or premium terms."
                    )
            elif income < 65000:
                affordability_note = (
                    "This area's household income sits below the national median, which often "
                    "means residents are more price-sensitive but also underserved by premium "
                    f"{service_category} providers — an opening for a value-focused offer."
                )
            else:
                affordability_note = (
                    "This area's household income is above-average, suggesting residents can "
                    f"support a broader range of {service_category} price points, including "
                    "premium service tiers."
                )
            lines.append(affordability_note)
            lines.append("")
    elif demographic_profile.get("counties"):
        lines.append(f"STATE OVERVIEW: {state} ({len(demographic_profile['counties'])} counties analyzed)")
        lines.append("-" * 60)
        top_counties = sorted(
            [c for c in demographic_profile["counties"] if c.get("population")],
            key=lambda c: c.get("population") or 0,
            reverse=True,
        )[:5]
        for c in top_counties:
            pop_str = f"{c['population']:,}" if c.get("population") else "N/A"
            income_str = f"${c['median_household_income']:,}" if c.get("median_household_income") else "N/A"
            lines.append(f"• {c['name']}: population {pop_str}, median income {income_str}")
        lines.append("")

    # Banking/economic climate section
    if lending_display:
        lines.append("CURRENT LENDING & ECONOMIC CLIMATE (National, via Federal Reserve FRED)")
        lines.append("-" * 60)
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
            lines.append(f"• {item['label']}: {display_value} (as of {item['as_of']})")
        lines.append("")

        # Contextual note about rates and lending appetite -- phrased
        # differently depending on whether this is a financial-services
        # pitch (loans, credit, lending) vs. a general home/local service.
        prime_rate_entry = next((i for i in lending_display if "Prime" in i["label"]), None)
        if prime_rate_entry:
            if business_name:
                audience_phrase = f"prospects considering {business_name}"
            else:
                audience_phrase = "local prospects"

            if is_financial_category:
                lines.append(
                    f"With the current bank prime rate at {prime_rate_entry['value']}%, borrowing costs "
                    f"are top of mind for {audience_phrase} right now. Businesses and consumers alike are "
                    f"actively comparing rates and terms, which makes this a strong window to lead with "
                    f"clear, competitive terms and fast approval turnaround as your differentiator."
                )
            else:
                lines.append(
                    f"With the current bank prime rate at {prime_rate_entry['value']}%, financing costs are "
                    f"a real factor in how {audience_phrase} make purchase decisions right now — "
                    f"positioning your offer around value and flexible payment terms can be a meaningful "
                    f"differentiator."
                )
            lines.append("")

    lines.append("=" * 60)
    lines.append(
        "Data sources: U.S. Census Bureau ACS 5-Year Estimates; Federal Reserve Bank of St. "
        "Louis (FRED) public economic database. All figures are public, aggregate statistics — "
        "no personal or private financial data was accessed."
    )
    lines.append(f"Generated {datetime.utcnow().strftime('%B %d, %Y')} by BizStack Perks.")

    return "\n".join(lines)
