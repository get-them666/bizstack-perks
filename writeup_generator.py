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
from banking_data import FredEconomicData, format_lending_snapshot_for_display, FRED_SERIES

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
      - FRED public banking/economic snapshot (rates, lending trends) plus
        trend direction over the past year
      - A long-form narrative tying market sizing, demographics, economic
        climate, competitive positioning, and concrete next steps together

    Returns a dict with both structured data (for an API/JSON response) and
    a formatted plain-text write-up (ready to copy into an email/SMS/call).
    """
    census = CensusLeadAnalyzer(census_api_key)
    fred = FredEconomicData(fred_api_key)

    demographic_profile = await census.get_area_demographic_profile(state, county_fips)
    lending_snapshot = await fred.get_lending_snapshot()
    lending_display = format_lending_snapshot_for_display(lending_snapshot)

    # Pull a 12-month trend for the two most decision-relevant series so the
    # write-up can say whether rates are rising, falling, or flat -- not
    # just a single snapshot number.
    prime_trend = await fred.get_series_trend(FRED_SERIES["prime_rate"], months=12)
    unemployment_trend = await fred.get_series_trend(FRED_SERIES["unemployment_rate"], months=12)

    narrative = _build_narrative(
        state=state,
        service_category=service_category,
        demographic_profile=demographic_profile,
        lending_display=lending_display,
        prime_trend=prime_trend,
        unemployment_trend=unemployment_trend,
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


def _trend_direction(trend: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """
    Given a list of {date, value} observations, describe the direction of
    change from the earliest to the latest reading. Returns None if there
    isn't enough data to compare.
    """
    usable = [t for t in trend if t.get("value") not in (None, "", ".")]
    if len(usable) < 2:
        return None

    try:
        first_val = float(usable[0]["value"])
        last_val = float(usable[-1]["value"])
    except (TypeError, ValueError):
        return None

    delta = last_val - first_val
    if abs(delta) < 0.05:
        direction = "held roughly steady"
    elif delta > 0:
        direction = "risen"
    else:
        direction = "fallen"

    return {
        "direction": direction,
        "delta": round(abs(delta), 2),
        "first_value": round(first_val, 2),
        "last_value": round(last_val, 2),
        "first_date": usable[0]["date"],
        "last_date": usable[-1]["date"],
    }


def _estimate_market_size(population: Optional[int], service_category: str) -> Optional[Dict[str, Any]]:
    """
    Rough, transparent order-of-magnitude market-sizing estimate based on
    population. This is intentionally simple and clearly labeled as an
    estimate -- not a substitute for real market research -- but gives a
    sales conversation a concrete number to anchor on instead of just
    "there are a lot of people here."
    """
    if not population:
        return None

    # Roughly estimate households (US average ~2.5 people/household) and
    # apply a conservative estimated annual addressable spend per household
    # for a generic local service category. This is a simplification meant
    # for directional sizing, not a certified market study.
    estimated_households = int(population / 2.5)
    estimated_annual_spend_per_household = 250  # conservative, generic local-service estimate
    estimated_annual_market_value = estimated_households * estimated_annual_spend_per_household

    return {
        "estimated_households": estimated_households,
        "estimated_annual_market_value": estimated_annual_market_value,
    }


def _build_narrative(
    state: str,
    service_category: str,
    demographic_profile: Dict[str, Any],
    lending_display: List[Dict[str, str]],
    prime_trend: Optional[List[Dict[str, str]]] = None,
    unemployment_trend: Optional[List[Dict[str, str]]] = None,
    business_name: Optional[str] = None,
) -> str:
    """Generate a long-form, professional, client-ready narrative from the raw data."""
    lines: List[str] = []

    category_lower = service_category.lower()
    is_financial_category = any(
        term in category_lower for term in ["loan", "credit", "lend", "financ", "mortgage"]
    )

    area_name = demographic_profile.get("name", state) if demographic_profile else state
    pop = demographic_profile.get("population") if demographic_profile else None
    income = demographic_profile.get("median_household_income") if demographic_profile else None
    age = demographic_profile.get("median_age") if demographic_profile else None
    housing = demographic_profile.get("housing_units") if demographic_profile else None

    prime_entry = next((i for i in lending_display if "Prime" in i["label"]), None)
    unemployment_entry = next((i for i in lending_display if "Unemployment" in i["label"]), None)
    prime_direction = _trend_direction(prime_trend) if prime_trend else None
    unemployment_direction = _trend_direction(unemployment_trend) if unemployment_trend else None

    # ------------------------------------------------------------------
    # Title
    # ------------------------------------------------------------------
    lines.append(f"MARKET OPPORTUNITY BRIEF — {service_category.title()} in {area_name}")
    lines.append("=" * 70)
    lines.append(f"Prepared {datetime.utcnow().strftime('%B %d, %Y')} | BizStack Perks Research")
    lines.append("")

    # ------------------------------------------------------------------
    # Executive summary
    # ------------------------------------------------------------------
    lines.append("EXECUTIVE SUMMARY")
    lines.append("-" * 70)
    summary_bits = []
    if pop:
        summary_bits.append(f"{area_name} has a population of approximately {pop:,}")
    if income:
        summary_bits.append(f"a median household income of ${income:,}")
    if age:
        summary_bits.append(f"a median age of {age}")
    if summary_bits:
        lines.append(
            f"{'; '.join(summary_bits)}. Combined with the current national lending "
            f"environment, this brief lays out where the opportunity for {service_category} "
            f"is strongest in this market, what economic headwinds or tailwinds to expect, "
            f"and concrete next steps for outreach."
        )
    else:
        lines.append(
            f"This brief summarizes current market conditions for {service_category} in "
            f"{area_name}, combining public demographic and economic data to inform an "
            f"outreach and pricing strategy."
        )
    lines.append("")

    # ------------------------------------------------------------------
    # Demographic deep dive
    # ------------------------------------------------------------------
    if demographic_profile and "counties" not in demographic_profile:
        lines.append(f"AREA PROFILE: {area_name}")
        lines.append("-" * 70)
        if pop:
            lines.append(f"• Population: {pop:,}")
        if income:
            lines.append(f"• Median household income: ${income:,}")
        if age:
            lines.append(f"• Median age: {age}")
        if housing:
            lines.append(f"• Housing units: {housing:,}")
            if pop:
                avg_household_size = round(pop / housing, 2) if housing else None
                if avg_household_size:
                    lines.append(f"• Average persons per housing unit: {avg_household_size}")
        lines.append("")

        # Market sizing estimate
        market_size = _estimate_market_size(pop, service_category)
        if market_size:
            lines.append("ESTIMATED MARKET SIZE (directional estimate, not a certified study)")
            lines.append("-" * 70)
            lines.append(
                f"• Estimated households in this area: ~{market_size['estimated_households']:,} "
                f"(based on population \u00f7 2.5, the approximate U.S. average household size)"
            )
            lines.append(
                f"• At a conservative estimated annual spend of $250/household on services like "
                f"{service_category}, the directional addressable market here is roughly "
                f"${market_size['estimated_annual_market_value']:,} per year. Actual figures vary "
                f"significantly by specific service and should be refined with real conversion data "
                f"once outreach begins."
            )
            lines.append("")

        # Demographic-driven positioning narrative
        if income and pop:
            lines.append("WHAT THE DEMOGRAPHICS MEAN FOR YOUR PITCH")
            lines.append("-" * 70)
            if is_financial_category:
                if income < 65000:
                    lines.append(
                        f"This area's median household income (${income:,}) sits below the national "
                        f"median (~$75,000), which typically means residents are more sensitive to fees "
                        f"and approval speed than to premium features. For {service_category}, leading "
                        f"with transparent terms, low or no origination fees, and fast approval turnaround "
                        f"tends to outperform a premium-positioned offer here. Price-anchoring against "
                        f"predatory or unclear alternatives (payday lenders, high-fee cards) can also be "
                        f"an effective message in lower-income markets."
                    )
                else:
                    lines.append(
                        f"This area's median household income (${income:,}) is above the national median, "
                        f"which suggests a meaningful share of prospects can qualify for larger loan "
                        f"amounts or premium terms. For {service_category}, this is a market where "
                        f"emphasizing higher credit limits, rewards, or premium service tiers is more "
                        f"likely to convert than a bare-bones, lowest-price pitch."
                    )
            else:
                if income < 65000:
                    lines.append(
                        f"This area's median household income (${income:,}) sits below the national "
                        f"median, which usually means residents are more price-sensitive but are also "
                        f"often underserved by premium {service_category} providers who focus on "
                        f"higher-income neighborhoods. That gap is an opening: a clearly value-priced "
                        f"offer, positioned as reliable and straightforward rather than premium, tends "
                        f"to convert well in markets like this."
                    )
                else:
                    lines.append(
                        f"This area's median household income (${income:,}) is above the national "
                        f"median, suggesting residents can support a broader range of price points for "
                        f"{service_category}, including premium tiers, bundled packages, or "
                        f"subscription/maintenance plans rather than one-off pricing."
                    )
            if age:
                if age < 35:
                    lines.append(
                        f"The median age here ({age}) is on the younger side, which often correlates "
                        f"with higher comfort using digital channels (SMS, online booking, app-based "
                        f"scheduling) over phone calls or in-person visits as the primary point of contact."
                    )
                elif age > 45:
                    lines.append(
                        f"The median age here ({age}) is on the older side, which often means phone "
                        f"calls and clear, in-person or over-the-phone explanations of pricing and terms "
                        f"tend to build more trust than digital-only outreach."
                    )
            lines.append("")
    elif demographic_profile.get("counties"):
        lines.append(f"STATE OVERVIEW: {state} ({len(demographic_profile['counties'])} counties analyzed)")
        lines.append("-" * 70)
        top_counties = sorted(
            [c for c in demographic_profile["counties"] if c.get("population")],
            key=lambda c: c.get("population") or 0,
            reverse=True,
        )[:8]
        for c in top_counties:
            pop_str = f"{c['population']:,}" if c.get("population") else "N/A"
            income_str = f"${c['median_household_income']:,}" if c.get("median_household_income") else "N/A"
            lines.append(f"• {c['name']}: population {pop_str}, median income {income_str}")
        lines.append("")
        lines.append(
            "For a deeper, single-county version of this brief with market sizing and demographic "
            "positioning notes, regenerate this report with a specific county FIPS code."
        )
        lines.append("")

    # ------------------------------------------------------------------
    # Economic climate (expanded, with trend direction)
    # ------------------------------------------------------------------
    if lending_display:
        lines.append("CURRENT LENDING & ECONOMIC CLIMATE (National, via Federal Reserve FRED)")
        lines.append("-" * 70)
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

        lines.append("WHAT THE ECONOMIC TRENDS MEAN")
        lines.append("-" * 70)

        if prime_direction and prime_entry:
            lines.append(
                f"The bank prime rate has {prime_direction['direction']} over the past year, moving "
                f"from {prime_direction['first_value']}% ({prime_direction['first_date']}) to "
                f"{prime_direction['last_value']}% ({prime_direction['last_date']}). "
            )
            if prime_direction["direction"] == "risen":
                lines.append(
                    "Rising rates generally make financing more expensive for both consumers and "
                    "businesses, which increases sensitivity to price and terms -- a good reason to "
                    "lead with clear, competitive pricing and flexible payment options right now."
                )
            elif prime_direction["direction"] == "fallen":
                lines.append(
                    "Falling rates generally make borrowing cheaper, which can loosen budgets and "
                    "increase willingness to spend on financed purchases or bundled service packages -- "
                    "a good window to introduce premium options or larger-ticket offers."
                )
            else:
                lines.append(
                    "A steady rate environment tends to mean fewer financing-driven objections, so "
                    "the sales conversation can focus more on service quality and fit than on price "
                    "anxiety."
                )
            lines.append("")

        if unemployment_direction and unemployment_entry:
            lines.append(
                f"The national unemployment rate has {unemployment_direction['direction']} over the "
                f"past year, from {unemployment_direction['first_value']}% "
                f"({unemployment_direction['first_date']}) to {unemployment_direction['last_value']}% "
                f"({unemployment_direction['last_date']})."
            )
            if unemployment_direction["direction"] == "risen":
                lines.append(
                    "Rising unemployment can mean tighter household budgets and more cautious spending "
                    "-- value-oriented messaging and flexible payment plans tend to perform better in "
                    "this environment."
                )
            elif unemployment_direction["direction"] == "fallen":
                lines.append(
                    "Falling unemployment generally signals more disposable income and consumer "
                    "confidence, supporting a broader range of price points."
                )
            lines.append("")

        # Contextual close tying prime rate to the specific pitch
        if prime_entry:
            if business_name:
                audience_phrase = f"prospects considering {business_name}"
            else:
                audience_phrase = "local prospects"

            if is_financial_category:
                lines.append(
                    f"Bottom line for outreach: with the prime rate at {prime_entry['value']}%, "
                    f"borrowing costs are top of mind for {audience_phrase} right now. Leading with "
                    f"clear terms, fast approval, and transparent fees is likely to outperform a "
                    f"rate-focused pitch alone, since most prospects will be comparing multiple offers."
                )
            else:
                lines.append(
                    f"Bottom line for outreach: with the prime rate at {prime_entry['value']}%, "
                    f"financing costs remain a real factor in how {audience_phrase} make purchase "
                    f"decisions. Positioning around value, flexible payment terms, and predictable "
                    f"pricing is likely to reduce sticker-shock objections."
                )
            lines.append("")

    # ------------------------------------------------------------------
    # Competitive positioning
    # ------------------------------------------------------------------
    lines.append("COMPETITIVE POSITIONING SUGGESTIONS")
    lines.append("-" * 70)
    lines.append(
        f"1. Lead with the specific signal that prompted this outreach (an expansion, a permit, a "
        f"news mention) rather than a generic cold pitch -- it shows you did real research and "
        f"immediately differentiates from mass-market solicitations."
    )
    lines.append(
        f"2. Anchor pricing/terms against the local economic conditions above, not just against "
        f"competitors -- prospects respond better to 'here's what makes sense given today's rates' "
        f"than to a generic discount pitch."
    )
    lines.append(
        f"3. If this is a repeat or ongoing service ({service_category}), consider a simple "
        f"subscription or maintenance-plan framing rather than one-off pricing -- it reduces the "
        f"perceived commitment on the first call and creates recurring revenue for you."
    )
    lines.append("")

    # ------------------------------------------------------------------
    # Next steps / call to action
    # ------------------------------------------------------------------
    lines.append("RECOMMENDED NEXT STEPS")
    lines.append("-" * 70)
    lines.append("1. Reach out within 3-5 business days of identifying the signal -- expansion and financing windows are time-sensitive.")
    lines.append("2. Reference the specific public signal in your first message (see the outreach generator for a ready-to-send draft).")
    lines.append("3. Offer a low-friction next step (a quick call, a free rate comparison, a sample lead) rather than asking for a commitment immediately.")
    lines.append("4. Log the outcome in your BizStack Perks dashboard so future write-ups for this area can factor in what worked.")
    lines.append("")

    # ------------------------------------------------------------------
    # Footer / sourcing
    # ------------------------------------------------------------------
    lines.append("=" * 70)
    lines.append(
        "Data sources: U.S. Census Bureau ACS 5-Year Estimates; Federal Reserve Bank of St. "
        "Louis (FRED) public economic database. All figures are public, aggregate statistics — "
        "no personal or private financial data was accessed. Market sizing figures are directional "
        "estimates based on population and a generic spend assumption, not a certified market study."
    )
    lines.append(f"Generated {datetime.utcnow().strftime('%B %d, %Y')} by BizStack Perks.")

    return "\n".join(lines)
