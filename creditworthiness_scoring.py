"""
Creditworthiness scoring for discovered business leads.

Uses public signals to score lead quality:
- Business age (older = more stable)
- Industry (some industries have higher default rates)
- Location (economic indicators from Census/FRED)
- News sentiment (recent expansion/growth signals)
- Business news mentions (frequency + recency)

Score: 0-100 scale
- 0-30: High risk
- 31-60: Medium risk
- 61-80: Good quality
- 81-100: Excellent quality (most likely to convert)
"""

import sqlite3
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

# Industry risk multipliers (lower = less risky for lending)
INDUSTRY_RISK = {
    "restaurants": 1.3,  # High failure rate
    "retail": 1.2,
    "construction": 0.9,
    "technology": 0.7,
    "consulting": 0.6,
    "financial": 0.5,
    "professional services": 0.6,
    "healthcare": 0.7,
    "manufacturing": 0.8,
    "real estate": 0.9,
    "default": 1.0,
}

# State credit quality (based on FRED/Census data)
STATE_CREDIT_QUALITY = {
    "CA": 0.85,
    "NY": 0.80,
    "TX": 0.82,
    "FL": 0.78,
    "MA": 0.87,
    "WA": 0.84,
    "IL": 0.75,
    "OH": 0.73,
    "PA": 0.76,
    "MI": 0.71,
    "default": 0.75,
}


def calculate_creditworthiness_score(
    business_name: str,
    industry: Optional[str] = None,
    location: Optional[str] = None,
    business_age_years: Optional[int] = None,
    recent_expansion_signals: int = 0,
    news_mentions_30_days: int = 0,
    request_amount: Optional[float] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """
    Calculate a creditworthiness score (0-100) for a business lead.

    Returns:
        {
            "score": 0-100,
            "rating": "High Risk" | "Medium Risk" | "Good" | "Excellent",
            "factors": {
                "business_stability": 0-20,
                "market_signals": 0-20,
                "industry_health": 0-20,
                "location_economics": 0-20,
                "request_reasonableness": 0-20,
            },
            "recommendation": "Pass" | "Review" | "Approve",
            "justification": "human-readable explanation"
        }
    """

    score_breakdown = {
        "business_stability": 0,
        "market_signals": 0,
        "industry_health": 0,
        "location_economics": 0,
        "request_reasonableness": 0,
    }

    # ========== Factor 1: Business Stability (0-20 points) ==========
    if business_age_years is None:
        business_age_years = 0
    
    if business_age_years >= 10:
        score_breakdown["business_stability"] = 20
    elif business_age_years >= 5:
        score_breakdown["business_stability"] = 15
    elif business_age_years >= 3:
        score_breakdown["business_stability"] = 10
    elif business_age_years >= 1:
        score_breakdown["business_stability"] = 5
    else:
        score_breakdown["business_stability"] = 0

    # ========== Factor 2: Market Signals (0-20 points) ==========
    signal_score = 0
    if recent_expansion_signals >= 3:
        signal_score = 20
    elif recent_expansion_signals == 2:
        signal_score = 15
    elif recent_expansion_signals == 1:
        signal_score = 10
    else:
        signal_score = 0

    if news_mentions_30_days > 0:
        mention_bonus = min(news_mentions_30_days * 2, 10)  # Cap at 10 points
        signal_score = min(signal_score + mention_bonus, 20)

    score_breakdown["market_signals"] = signal_score

    # ========== Factor 3: Industry Health (0-20 points) ==========
    industry_multiplier = INDUSTRY_RISK.get(
        (industry or "").lower(), INDUSTRY_RISK["default"]
    )
    industry_base_score = 15
    industry_adjusted = industry_base_score * (1 / industry_multiplier)
    score_breakdown["industry_health"] = min(round(industry_adjusted), 20)

    # ========== Factor 4: Location Economics (0-20 points) ==========
    location_quality = STATE_CREDIT_QUALITY.get("default", 0.75)
    if location:
        state_code = location.upper()[:2]
        location_quality = STATE_CREDIT_QUALITY.get(state_code, STATE_CREDIT_QUALITY["default"])

    location_score = round(location_quality * 20)
    score_breakdown["location_economics"] = location_score

    # ========== Factor 5: Request Reasonableness (0-20 points) ==========
    # If request_amount is reasonable relative to business type, award points
    request_score = 10  # Base score for having a request
    if request_amount is not None:
        if request_amount < 50000:
            request_score = 20  # Small, conservative request
        elif request_amount < 250000:
            request_score = 15  # Moderate request
        elif request_amount < 1000000:
            request_score = 10  # Larger request (needs more scrutiny)
        else:
            request_score = 5  # Very large request (high risk)

    score_breakdown["request_reasonableness"] = request_score

    # ========== Total Score ==========
    total_score = sum(score_breakdown.values())

    # ========== Rating & Recommendation ==========
    if total_score >= 81:
        rating = "Excellent"
        recommendation = "Approve"
    elif total_score >= 61:
        rating = "Good"
        recommendation = "Approve"
    elif total_score >= 31:
        rating = "Medium Risk"
        recommendation = "Review"
    else:
        rating = "High Risk"
        recommendation = "Pass"

    # ========== Justification ==========
    justifications = []
    if score_breakdown["business_stability"] < 10:
        justifications.append("Business is new or age unknown")
    if score_breakdown["market_signals"] < 5:
        justifications.append("Limited growth signals detected")
    if score_breakdown["industry_health"] < 10:
        justifications.append(f"Industry '{industry}' has higher risk profile")
    if score_breakdown["location_economics"] < 12:
        justifications.append("Location has weaker economic indicators")
    if score_breakdown["request_reasonableness"] < 10:
        justifications.append("Loan request amount is larger than typical for industry")

    if not justifications:
        if total_score >= 81:
            justifications = ["Strong business fundamentals and market position"]
        elif total_score >= 61:
            justifications = ["Solid business profile with positive growth signals"]
        else:
            justifications = ["Mixed signals; recommend further due diligence"]

    return {
        "score": total_score,
        "rating": rating,
        "factors": score_breakdown,
        "recommendation": recommendation,
        "justification": "; ".join(justifications),
    }


def score_lead(
    conn: sqlite3.Connection,
    lead_id: int,
    business_name: str,
    industry: Optional[str] = None,
    location: Optional[str] = None,
    business_age_years: Optional[int] = None,
    request_amount: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Score a lead and store the result in the database.
    
    Returns the scoring result dict.
    """
    # Look up any stored signals for this business
    cursor = conn.execute(
        """
        SELECT COUNT(*) as signal_count, 
               COUNT(CASE WHEN published_at >= datetime('now', '-30 days') THEN 1 END) as recent_signals
        FROM business_signals
        WHERE LOWER(business_name) = LOWER(?)
        """,
        (business_name,),
    )
    signal_row = cursor.fetchone()
    recent_expansion_signals = signal_row["recent_signals"] if signal_row else 0
    news_mentions_30_days = signal_row["signal_count"] if signal_row else 0

    result = calculate_creditworthiness_score(
        business_name=business_name,
        industry=industry,
        location=location,
        business_age_years=business_age_years,
        recent_expansion_signals=min(recent_expansion_signals, 3),  # Cap at 3
        news_mentions_30_days=news_mentions_30_days,
        request_amount=request_amount,
        conn=conn,
    )

    # Store score in leads table
    conn.execute(
        """
        UPDATE leads
        SET creditworthiness_score = ?,
            creditworthiness_rating = ?,
            creditworthiness_json = ?,
            scored_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            result["score"],
            result["rating"],
            json.dumps(result),
            lead_id,
        ),
    )
    conn.commit()

    return result


def get_lead_score(conn: sqlite3.Connection, lead_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve the stored creditworthiness score for a lead."""
    cursor = conn.execute(
        """
        SELECT creditworthiness_json
        FROM leads
        WHERE id = ?
        """,
        (lead_id,),
    )
    row = cursor.fetchone()
    if row and row["creditworthiness_json"]:
        return json.loads(row["creditworthiness_json"])
    return None


def init_scoring_schema(conn: sqlite3.Connection) -> None:
    """Add scoring columns to leads table if they don't exist."""
    cursor = conn.execute("PRAGMA table_info(leads)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    if "creditworthiness_score" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN creditworthiness_score REAL DEFAULT NULL")
    if "creditworthiness_rating" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN creditworthiness_rating TEXT DEFAULT NULL")
    if "creditworthiness_json" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN creditworthiness_json TEXT DEFAULT NULL")
    if "scored_at" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN scored_at TIMESTAMP DEFAULT NULL")

    conn.commit()
    logger.info("Creditworthiness scoring schema initialized")
