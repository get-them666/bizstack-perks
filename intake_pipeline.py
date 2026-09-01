"""
Client Intake Pipeline — end-to-end from form submission to email draft.

Flow:
  1. Receive client intake form (loan/credit card request + credit profile)
  2. Pull live FRED rate data + US Census demographic data for client's state/area
  3. Score the credit profile and compute relevant metrics (DTI estimate, note rate context)
  4. Draft a detailed, personalized email for admin review
  5. Store the draft in the DB as status='pending'
  6. Admin reviews via /admin/pipeline, edits if needed, then approves → sends via SMTP

No personal financial advice is given. All rate data is public FRED data.
All demographic data is public Census ACS data. The email is an informational
briefing only — it explicitly states it is not a credit decision.
"""

import os
import logging
import sqlite3
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List

from banking_data import FredEconomicData, FRED_SERIES
from lead_sources import CensusLeadAnalyzer

logger = logging.getLogger(__name__)

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SENDER_COMPANY_NAME = os.getenv("SENDER_COMPANY_NAME", "BizStack Perks")

# ── Human-readable product names ──────────────────────────────────────────────
PRODUCT_DISPLAY = {
    "business_line_of_credit": "Business Line of Credit",
    "business_term_loan": "Business Term Loan",
    "sba_loan": "SBA Loan (7a / 504)",
    "equipment_financing": "Equipment Financing",
    "invoice_factoring": "Invoice Factoring / AR Financing",
    "merchant_cash_advance": "Merchant Cash Advance",
    "business_credit_card": "Business Credit Card",
    "commercial_real_estate": "Commercial Real Estate Loan",
    "personal_loan": "Personal Loan",
    "mortgage": "Mortgage / Home Purchase",
    "refinance": "Mortgage Refinance",
    "home_equity": "Home Equity / HELOC",
    "auto_loan": "Auto Loan",
    "personal_credit_card": "Personal Credit Card",
    "debt_consolidation": "Debt Consolidation",
    "student_loan_refi": "Student Loan Refinance",
}

# ── FRED series that matter for each product ──────────────────────────────────
PRODUCT_RELEVANT_SERIES = {
    "mortgage": ["mortgage_30yr", "fed_funds_rate", "unemployment_rate"],
    "refinance": ["mortgage_30yr", "fed_funds_rate", "unemployment_rate"],
    "home_equity": ["mortgage_30yr", "prime_rate", "consumer_credit"],
    "personal_loan": ["prime_rate", "fed_funds_rate", "consumer_credit", "unemployment_rate"],
    "personal_credit_card": ["prime_rate", "consumer_credit", "delinquency_rate_business"],
    "auto_loan": ["prime_rate", "fed_funds_rate", "consumer_credit"],
    "debt_consolidation": ["prime_rate", "fed_funds_rate", "consumer_credit", "unemployment_rate"],
    "student_loan_refi": ["prime_rate", "fed_funds_rate"],
    "business_line_of_credit": ["prime_rate", "fed_funds_rate", "business_loans", "delinquency_rate_business", "small_business_optimism"],
    "business_term_loan": ["prime_rate", "fed_funds_rate", "business_loans", "delinquency_rate_business"],
    "sba_loan": ["prime_rate", "fed_funds_rate", "business_loans", "small_business_optimism"],
    "equipment_financing": ["prime_rate", "fed_funds_rate", "business_loans"],
    "invoice_factoring": ["prime_rate", "fed_funds_rate", "business_loans"],
    "merchant_cash_advance": ["prime_rate", "fed_funds_rate", "small_business_optimism"],
    "business_credit_card": ["prime_rate", "fed_funds_rate", "business_loans"],
    "commercial_real_estate": ["mortgage_30yr", "prime_rate", "fed_funds_rate", "business_loans"],
}

# ── Credit tier classification ─────────────────────────────────────────────────
def classify_credit_tier(score_range: str) -> tuple[str, str]:
    """Returns (tier_key, tier_label)."""
    if not score_range or score_range == "unknown":
        return "unknown", "Unknown"
    low = int(score_range.split("-")[0])
    if low >= 750:
        return "good", "Excellent"
    if low >= 700:
        return "good", "Good"
    if low >= 660:
        return "fair", "Fair"
    if low >= 620:
        return "fair", "Below Average"
    return "poor", "Poor / Subprime"


# ── DB init ────────────────────────────────────────────────────────────────────
def init_pipeline_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            client_email TEXT NOT NULL,
            client_phone TEXT,
            business_name TEXT,
            state TEXT NOT NULL,
            zip_code TEXT,
            product_type TEXT NOT NULL,
            requested_amount REAL,
            loan_purpose TEXT,
            desired_term_months INTEGER,
            credit_score_range TEXT,
            annual_income_revenue REAL,
            years_in_business TEXT,
            monthly_debt_payments REAL,
            collateral_available TEXT,
            bankruptcy_history TEXT,
            notes TEXT,
            urgency TEXT,
            fred_snapshot_json TEXT,
            census_snapshot_json TEXT,
            email_subject TEXT,
            email_body TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


# ── FRED data pull ─────────────────────────────────────────────────────────────
async def pull_fred_data(product_type: str) -> Dict[str, Any]:
    """Pull FRED rate data relevant to the product type. Returns formatted display dict."""
    fred = FredEconomicData(FRED_API_KEY)
    if not fred.is_configured():
        return {}

    series_keys = PRODUCT_RELEVANT_SERIES.get(product_type, list(FRED_SERIES.keys()))

    # Fetch snapshot and trend concurrently
    snapshot_task = fred.get_lending_snapshot()
    snapshot = await snapshot_task

    # Also pull 6-month trend on the primary rate series for trend direction
    primary_key = series_keys[0] if series_keys else "fed_funds_rate"
    primary_series_id = FRED_SERIES.get(primary_key, "FEDFUNDS")
    trend_history = await fred.get_series_trend(primary_series_id, months=6)

    # Determine trend direction from history
    trend = "flat"
    if len(trend_history) >= 2:
        try:
            first = float(trend_history[0]["value"])
            last = float(trend_history[-1]["value"])
            if last > first + 0.1:
                trend = "up"
            elif last < first - 0.1:
                trend = "down"
        except (ValueError, TypeError):
            pass

    LABELS = {
        "mortgage_30yr": "30-Year Fixed Mortgage Rate",
        "fed_funds_rate": "Federal Funds Rate",
        "prime_rate": "Bank Prime Loan Rate",
        "business_loans": "Commercial & Industrial Loans (All Banks)",
        "delinquency_rate_business": "Business Loan Delinquency Rate",
        "consumer_credit": "Total Consumer Credit Outstanding",
        "unemployment_rate": "National Unemployment Rate",
        "small_business_optimism": "Leading Economic Index",
    }
    UNITS = {
        "mortgage_30yr": "%", "fed_funds_rate": "%", "prime_rate": "%",
        "business_loans": "$B", "delinquency_rate_business": "%",
        "consumer_credit": "$M", "unemployment_rate": "%", "small_business_optimism": "index",
    }

    result = {}
    for key in series_keys:
        raw = snapshot.get(key)
        if not raw:
            continue
        val_str = raw.get("value", "")
        try:
            val_float = float(val_str)
            unit = UNITS.get(key, "")
            if unit in ("%",):
                display_val = f"{val_float:.2f}%"
            elif unit == "$B":
                display_val = f"${val_float:,.1f}B"
            elif unit == "$M":
                display_val = f"${val_float:,.0f}M"
            else:
                display_val = f"{val_float:.2f}"
        except (ValueError, TypeError):
            display_val = val_str or "N/A"

        result[key] = {
            "label": LABELS.get(key, key),
            "value": display_val,
            "raw": val_str,
            "as_of": raw.get("date", ""),
            "trend": trend if key == primary_key else "flat",
        }

    return result


# ── Census data pull ───────────────────────────────────────────────────────────
async def pull_census_data(state: str, zip_code: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Pull Census ACS demographic data for the client's state.
    Uses CensusLeadAnalyzer.get_area_demographic_profile() which returns
    {"counties": [{"name", "population", "median_household_income", "median_age", "housing_units"}, ...]}
    We aggregate across counties to compute state-level averages.
    """
    if not CENSUS_API_KEY:
        return []

    analyzer = CensusLeadAnalyzer(CENSUS_API_KEY)
    try:
        profile = await analyzer.get_area_demographic_profile(state)
        if not profile:
            return []

        counties = profile.get("counties", [])
        if not counties:
            # Might be a single-county response
            counties = [profile]

        # Aggregate: sum population, weighted-average income & age, sum housing
        total_pop = 0
        total_housing = 0
        income_vals = []
        age_vals = []

        for c in counties:
            pop = c.get("population") or 0
            total_pop += pop
            total_housing += c.get("housing_units") or 0
            if c.get("median_household_income"):
                income_vals.append(c["median_household_income"])
            if c.get("median_age"):
                age_vals.append(c["median_age"])

        rows = []
        if total_pop:
            rows.append({
                "label": "Total Population",
                "value": f"{total_pop:,}",
                "context": f"All counties, state of {state}",
            })
        if income_vals:
            med_income = int(sum(income_vals) / len(income_vals))
            rows.append({
                "label": "Avg. Median Household Income",
                "value": f"${med_income:,}",
                "context": "Census ACS 5-yr estimate, county average",
            })
        if age_vals:
            med_age = round(sum(age_vals) / len(age_vals), 1)
            rows.append({
                "label": "Avg. Median Age",
                "value": f"{med_age}",
                "context": "Years, county average",
            })
        if total_housing:
            rows.append({
                "label": "Total Housing Units",
                "value": f"{total_housing:,}",
                "context": f"All counties, state of {state}",
            })
        rows.append({
            "label": "Counties Analyzed",
            "value": str(len(counties)),
            "context": f"County-level data available for {state}",
        })

        return rows
    except Exception as e:
        logger.error(f"Census data pull failed for state {state}: {e}")
        return []


# ── Email draft generation ─────────────────────────────────────────────────────
def _build_email_draft(
    *,
    client_name: str,
    business_name: Optional[str],
    product_type: str,
    requested_amount: Optional[float],
    desired_term_months: Optional[int],
    credit_score_range: str,
    annual_income_revenue: Optional[float],
    monthly_debt_payments: Optional[float],
    years_in_business: Optional[str],
    collateral_available: Optional[str],
    bankruptcy_history: Optional[str],
    loan_purpose: Optional[str],
    state: str,
    zip_code: Optional[str],
    urgency: Optional[str],
    notes: Optional[str],
    fred_data: Dict[str, Any],
    census_data: List[Dict[str, str]],
) -> tuple[str, str]:
    """Build email subject and body. Returns (subject, body)."""

    product_display = PRODUCT_DISPLAY.get(product_type, product_type.replace("_", " ").title())
    credit_tier, credit_label = classify_credit_tier(credit_score_range)
    name_first = client_name.split()[0] if client_name else client_name
    entity = business_name or client_name
    location = f"{state}{' ' + zip_code if zip_code else ''}"
    now_str = datetime.now().strftime("%B %d, %Y")

    # Subject
    subject = f"Rate & Market Analysis — {product_display} for {entity} ({location})"

    # ── Body ──
    lines = []
    lines.append(f"Dear {name_first},")
    lines.append("")
    lines.append(
        f"Thank you for your interest in a {product_display}. I've put together a "
        f"current rate environment and market context briefing for your review — based on "
        f"live Federal Reserve data and US Census demographic information for {location}."
    )
    lines.append("")

    # Request summary
    lines.append("─" * 55)
    lines.append("YOUR REQUEST AT A GLANCE")
    lines.append("─" * 55)
    lines.append(f"Product:         {product_display}")
    if requested_amount:
        lines.append(f"Requested:       ${requested_amount:,.0f}")
    if desired_term_months:
        years = desired_term_months // 12
        months_rem = desired_term_months % 12
        term_str = f"{years}yr" if not months_rem else f"{desired_term_months}mo"
        lines.append(f"Desired term:    {term_str}")
    if loan_purpose:
        lines.append(f"Purpose:         {loan_purpose}")
    lines.append(f"Credit profile:  {credit_score_range or 'Unknown'} ({credit_label})")
    if annual_income_revenue:
        lines.append(f"Annual income/revenue: ${annual_income_revenue:,.0f}")
    if urgency and urgency != "flexible":
        urgency_map = {
            "30_days": "Within 30 days",
            "2_weeks": "Within 2 weeks",
            "immediate": "Immediate / ASAP",
        }
        lines.append(f"Timeline:        {urgency_map.get(urgency, urgency)}")
    lines.append("")

    # DTI estimate
    if monthly_debt_payments and annual_income_revenue and annual_income_revenue > 0:
        monthly_income = annual_income_revenue / 12
        dti = (monthly_debt_payments / monthly_income) * 100
        dti_note = "✓ Within typical guideline (<43%)" if dti < 43 else "⚠ Above the 43% DTI guideline most lenders use"
        lines.append(f"Estimated DTI (current debt / income): {dti:.1f}% — {dti_note}")
        if requested_amount and desired_term_months:
            # Very rough payment estimate at prime-ish rate
            est_rate = 0.07  # placeholder — real rate pulled from FRED below if available
            prime_raw = fred_data.get("prime_rate", {}).get("raw")
            if prime_raw:
                try:
                    est_rate = float(prime_raw) / 100 + 0.03  # spread above prime
                except (ValueError, TypeError):
                    pass
            monthly_rate = est_rate / 12
            n = desired_term_months
            if monthly_rate > 0:
                payment = requested_amount * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1)
                new_dti = ((monthly_debt_payments + payment) / monthly_income) * 100
                lines.append(
                    f"Estimated new DTI if approved at ~{est_rate*100:.1f}% rate: "
                    f"{new_dti:.1f}% (est. payment ~${payment:,.0f}/mo)"
                )
        lines.append("")

    # FRED rate environment
    if fred_data:
        lines.append("─" * 55)
        lines.append("CURRENT RATE ENVIRONMENT (Federal Reserve — FRED)")
        lines.append("─" * 55)
        for key, item in fred_data.items():
            trend_str = ""
            if item.get("trend") == "up":
                trend_str = " ↑ (rising over past 6 months)"
            elif item.get("trend") == "down":
                trend_str = " ↓ (falling over past 6 months)"
            as_of = f" as of {item['as_of']}" if item.get("as_of") else ""
            lines.append(f"• {item['label']}: {item['value']}{trend_str}{as_of}")
        lines.append("")
        lines.append(
            "What this means for you: These are nationwide benchmark rates published by the "
            "Federal Reserve. Individual lenders set their own rates above these benchmarks based "
            "on your specific credit profile, collateral, and underwriting criteria."
        )
        lines.append("")

    # Census demographics
    if census_data:
        lines.append("─" * 55)
        lines.append(f"AREA MARKET SNAPSHOT — {state} (US Census ACS)")
        lines.append("─" * 55)
        for row in census_data:
            lines.append(f"• {row['label']}: {row['value']}  ({row['context']})")
        lines.append("")
        lines.append(
            "This demographic snapshot provides context on the market you're operating in — "
            "income levels, homeownership rates, and population density all influence the "
            "products and terms available in your area."
        )
        lines.append("")

    # Credit profile notes
    lines.append("─" * 55)
    lines.append("CREDIT PROFILE NOTES")
    lines.append("─" * 55)
    if credit_tier == "good":
        lines.append(
            f"Your estimated credit range ({credit_score_range}) is in the {credit_label} tier. "
            "This typically qualifies for competitive rates and a wider range of lender options."
        )
    elif credit_tier == "fair":
        lines.append(
            f"Your estimated credit range ({credit_score_range}) is in the {credit_label} tier. "
            "Many lenders work with this range, though rates may be somewhat higher than prime. "
            "Demonstrating strong income and low DTI can offset score concerns."
        )
    else:
        lines.append(
            f"Your estimated credit range ({credit_score_range}) is in the {credit_label} tier. "
            "Traditional bank products may be limited, but alternative lenders (business cash "
            "advance, asset-backed financing, secured products) often work with this profile."
        )

    if collateral_available and collateral_available not in ("no", "unsecured_preferred", ""):
        collateral_map = {
            "yes_real_estate": "real estate",
            "yes_equipment": "equipment or vehicles",
            "yes_receivables": "accounts receivable / inventory",
            "yes_other": "collateral assets",
        }
        coll_label = collateral_map.get(collateral_available, "collateral")
        lines.append(
            f"You've indicated {coll_label} may be available as collateral — this is a "
            "strong positive for secured lending products and typically improves rate offers."
        )

    if bankruptcy_history and bankruptcy_history != "none":
        bk_notes = {
            "discharged_2plus": "A discharged bankruptcy (2+ years ago) is workable with many lenders, especially for secured or alternative products.",
            "discharged_recent": "A recently discharged bankruptcy narrows lender options significantly. Secured products and credit-building strategies are the best starting point.",
            "active": "An active bankruptcy requires lender approval; options are limited but some specialized lenders work with active BK cases for certain secured products.",
            "unknown": "If there is any bankruptcy history, it's worth confirming the status before applying, as it directly affects product eligibility.",
        }
        lines.append(bk_notes.get(bankruptcy_history, ""))

    if years_in_business:
        yib_map = {
            "less_than_1": "less than 1 year",
            "1-2": "1–2 years",
            "2-5": "2–5 years",
            "5-10": "5–10 years",
            "10+": "10+ years",
        }
        lines.append(
            f"Time in business/employment ({yib_map.get(years_in_business, years_in_business)}) "
            "is one of the key underwriting factors. Many lenders require 2+ years."
        )

    lines.append("")

    if notes:
        lines.append("─" * 55)
        lines.append("ADDITIONAL NOTES")
        lines.append("─" * 55)
        lines.append(notes)
        lines.append("")

    # Next steps
    lines.append("─" * 55)
    lines.append("NEXT STEPS")
    lines.append("─" * 55)
    lines.append(
        "Based on your profile and the current rate environment, I'd recommend we schedule "
        "a brief call to walk through the best-fit products for your situation. I can connect "
        "you with the right lenders for your specific needs."
    )
    lines.append("")
    lines.append("Please reply to this email or call us to get started.")
    lines.append("")
    lines.append(f"Best regards,")
    lines.append(f"{SENDER_COMPANY_NAME}")
    lines.append("")
    lines.append("─" * 55)
    lines.append(
        "DISCLOSURE: This email is an informational briefing only. It is NOT a credit application, "
        "credit decision, loan approval, or guarantee of any rate or terms. All rate data is sourced "
        "from the Federal Reserve Bank of St. Louis (FRED) public database. Demographic data is sourced "
        "from the US Census Bureau American Community Survey. Individual rates vary based on lender "
        "underwriting, credit review, and market conditions at time of application."
    )

    return subject, "\n".join(lines)


# ── OpenAI-enhanced draft (optional upgrade) ──────────────────────────────────
async def _enhance_draft_with_ai(subject: str, body: str, context: Dict[str, Any]) -> tuple[str, str]:
    """If OpenAI is configured, use it to polish the email into a more natural tone."""
    if not OPENAI_API_KEY:
        return subject, body

    try:
        import httpx

        prompt = f"""You are a financial services email specialist. Rewrite the following client briefing
email to be warm, professional, and conversational — while preserving ALL data points, numbers, rates,
and disclosure language exactly. Do not invent new data. Keep the same section structure but use natural
paragraph prose instead of raw line-by-line lists where possible. Keep length similar.

ORIGINAL EMAIL:
Subject: {subject}

{body}

Return ONLY the rewritten email body (no subject line, no commentary)."""

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500,
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            enhanced_body = resp.json()["choices"][0]["message"]["content"].strip()
            return subject, enhanced_body
    except Exception as e:
        logger.warning(f"OpenAI draft enhancement failed (using template draft): {e}")
        return subject, body


# ── Main pipeline runner ───────────────────────────────────────────────────────
async def run_intake_pipeline(
    *,
    conn: sqlite3.Connection,
    client_name: str,
    client_email: str,
    client_phone: Optional[str],
    business_name: Optional[str],
    state: str,
    zip_code: Optional[str],
    product_type: str,
    requested_amount: Optional[float],
    loan_purpose: Optional[str],
    desired_term_months: Optional[int],
    credit_score_range: str,
    annual_income_revenue: Optional[float],
    years_in_business: Optional[str],
    monthly_debt_payments: Optional[float],
    collateral_available: Optional[str],
    bankruptcy_history: Optional[str],
    notes: Optional[str],
    urgency: Optional[str],
) -> int:
    """
    Full pipeline: pull data → build draft → store in DB.
    Returns the new intake_pipeline row ID.
    """
    import json

    # Pull external data concurrently
    fred_task = pull_fred_data(product_type)
    census_task = pull_census_data(state, zip_code)
    fred_data, census_data = await asyncio.gather(fred_task, census_task)

    # Build template draft
    subject, body = _build_email_draft(
        client_name=client_name,
        business_name=business_name,
        product_type=product_type,
        requested_amount=requested_amount,
        desired_term_months=desired_term_months,
        credit_score_range=credit_score_range,
        annual_income_revenue=annual_income_revenue,
        monthly_debt_payments=monthly_debt_payments,
        years_in_business=years_in_business,
        collateral_available=collateral_available,
        bankruptcy_history=bankruptcy_history,
        loan_purpose=loan_purpose,
        state=state,
        zip_code=zip_code,
        urgency=urgency,
        notes=notes,
        fred_data=fred_data,
        census_data=census_data,
    )

    # Optional AI polish
    subject, body = await _enhance_draft_with_ai(subject, body, {})

    # Persist to DB
    cursor = conn.execute(
        """
        INSERT INTO intake_pipeline (
            client_name, client_email, client_phone, business_name,
            state, zip_code, product_type, requested_amount,
            loan_purpose, desired_term_months, credit_score_range,
            annual_income_revenue, years_in_business, monthly_debt_payments,
            collateral_available, bankruptcy_history, notes, urgency,
            fred_snapshot_json, census_snapshot_json,
            email_subject, email_body, status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')
        """,
        (
            client_name, client_email, client_phone, business_name,
            state, zip_code, product_type, requested_amount,
            loan_purpose, desired_term_months, credit_score_range,
            annual_income_revenue, years_in_business, monthly_debt_payments,
            collateral_available, bankruptcy_history, notes, urgency,
            json.dumps(fred_data), json.dumps(census_data),
            subject, body,
        ),
    )
    conn.commit()
    new_id = cursor.lastrowid

    # Auto-text client to confirm intake received
    _send_pipeline_intro_sms(client_phone, client_name, product_type)

    return new_id


def _send_pipeline_intro_sms(phone: Optional[str], client_name: str, product_type: str) -> None:
    """Send a brief confirmation text to the client when their intake is processed."""
    if not phone:
        return

    import os
    try:
        from twilio.rest import Client as TwilioClient

        account_sid = os.getenv("SIGNALWIRE_PROJECT_ID") or os.getenv("TWILIO_ACCOUNT_SID", "")
        auth_token  = os.getenv("SIGNALWIRE_API_TOKEN")  or os.getenv("TWILIO_AUTH_TOKEN", "")
        from_number = (
            os.getenv("SIGNALWIRE_PHONE_NUMBER")
            or os.getenv("TWILIO_PHONE_NUMBER")
            or os.getenv("TWILIO_NUMBER", "")
        )
        space_url   = os.getenv("SIGNALWIRE_SPACE_URL", "")

        if not (account_sid and auth_token and from_number):
            return

        first_name   = client_name.split()[0] if client_name else "there"
        product_disp = PRODUCT_DISPLAY.get(product_type, product_type.replace("_", " ").title())
        msg = (
            f"Hi {first_name}, this is Sam at BizStack Perks. We've received your {product_disp} "
            f"intake and are pulling live rate data for your area now. We'll have a detailed "
            f"briefing ready for you shortly. Questions? Just reply here. Reply STOP to opt out."
        )

        client = TwilioClient(account_sid, auth_token)
        if space_url:
            space_url = space_url.strip().rstrip("/")
            if not space_url.startswith("http"):
                space_url = f"https://{space_url}"
            client.api.base_url = space_url

        client.messages.create(body=msg, from_=from_number, to=phone)
        logger.info("Pipeline intro SMS sent to %s", phone)
    except Exception as e:
        logger.warning("Pipeline intro SMS failed (non-fatal): %s", e)


def get_pipeline_item(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM intake_pipeline WHERE id = ?", (item_id,)).fetchone()


def get_pipeline_queue(conn: sqlite3.Connection, limit: int = 200) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM intake_pipeline ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def mark_pipeline_sent(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute(
        "UPDATE intake_pipeline SET status='sent', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (item_id,),
    )
    conn.commit()


def mark_pipeline_discarded(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute(
        "UPDATE intake_pipeline SET status='discarded', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (item_id,),
    )
    conn.commit()
