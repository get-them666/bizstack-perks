"""
Tax reference data: federal income tax brackets, standard deductions, and
self-employment tax rates for use in market write-ups and general reference.

IMPORTANT: There is no free, real-time public IRS API for this kind of
general reference data (tax brackets, deductions, SE tax rates). The IRS
publishes these figures annually as PDFs/tables, not as a queryable API.
This module is a manually-maintained, curated reference -- same pattern as
bank_rates.json -- sourced from IRS.gov's own published tables. It is NOT
scraped, and it is NOT a substitute for consulting a CPA or tax attorney.

Update TAX_YEAR and the figures below each year when the IRS publishes new
brackets (typically announced in the fall for the following tax year).
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

TAX_YEAR = 2025  # Update annually when the IRS publishes new figures
SOURCE_NOTE = (
    "Figures sourced from IRS.gov published tables (Rev. Proc. 2024-40 and related IRS "
    "guidance for tax year 2025). Verify current figures directly at irs.gov before "
    "relying on them -- tax law and inflation adjustments can change these numbers."
)

# 2025 federal income tax brackets (Married Filing Jointly and Single).
# Source: IRS Rev. Proc. 2024-40, published tables for tax year 2025.
FEDERAL_TAX_BRACKETS_2025: Dict[str, List[Dict[str, Any]]] = {
    "single": [
        {"rate": 0.10, "up_to": 11925},
        {"rate": 0.12, "up_to": 48475},
        {"rate": 0.22, "up_to": 103350},
        {"rate": 0.24, "up_to": 197300},
        {"rate": 0.32, "up_to": 250525},
        {"rate": 0.35, "up_to": 626350},
        {"rate": 0.37, "up_to": None},  # no upper limit
    ],
    "married_filing_jointly": [
        {"rate": 0.10, "up_to": 23850},
        {"rate": 0.12, "up_to": 96950},
        {"rate": 0.22, "up_to": 206700},
        {"rate": 0.24, "up_to": 394600},
        {"rate": 0.32, "up_to": 501050},
        {"rate": 0.35, "up_to": 751600},
        {"rate": 0.37, "up_to": None},
    ],
}

# 2025 standard deduction amounts.
STANDARD_DEDUCTION_2025 = {
    "single": 15000,
    "married_filing_jointly": 30000,
    "head_of_household": 22500,
}

# Self-employment tax (Social Security + Medicare) -- rate is stable year to
# year unless Congress changes it; the Social Security WAGE BASE (the income
# ceiling for the Social Security portion) is adjusted annually.
SELF_EMPLOYMENT_TAX = {
    "total_rate": 0.153,  # 15.3% = 12.4% Social Security + 2.9% Medicare
    "social_security_rate": 0.124,
    "medicare_rate": 0.029,
    "social_security_wage_base_2025": 176100,
    "additional_medicare_tax_rate": 0.009,  # applies above threshold
    "additional_medicare_threshold_single": 200000,
    "additional_medicare_threshold_mfj": 250000,
}

# Standard business mileage rate (useful for local service business context).
STANDARD_MILEAGE_RATE_2025 = 0.70  # dollars per mile, business use


def get_tax_year() -> int:
    return TAX_YEAR


def get_brackets(filing_status: str = "single") -> List[Dict[str, Any]]:
    """Get federal tax brackets for a filing status ('single' or 'married_filing_jointly')."""
    return FEDERAL_TAX_BRACKETS_2025.get(filing_status, FEDERAL_TAX_BRACKETS_2025["single"])


def get_standard_deduction(filing_status: str = "single") -> Optional[int]:
    return STANDARD_DEDUCTION_2025.get(filing_status)


def estimate_effective_tax_rate(taxable_income: float, filing_status: str = "single") -> Dict[str, Any]:
    """
    Rough educational estimate of federal income tax owed under the 2025
    brackets, using marginal-bracket math. This is a SIMPLIFIED estimate for
    general reference only -- it does not account for credits, additional
    taxes, state tax, or many other real-world factors. Not tax advice.
    """
    brackets = get_brackets(filing_status)
    remaining = max(taxable_income, 0)
    tax_owed = 0.0
    previous_cap = 0

    for bracket in brackets:
        rate = bracket["rate"]
        cap = bracket["up_to"]
        if cap is None:
            taxable_in_bracket = remaining
        else:
            taxable_in_bracket = max(0, min(remaining, cap - previous_cap))

        tax_owed += taxable_in_bracket * rate
        remaining -= taxable_in_bracket
        previous_cap = cap if cap is not None else previous_cap

        if remaining <= 0:
            break

    effective_rate = (tax_owed / taxable_income) if taxable_income > 0 else 0

    return {
        "taxable_income": taxable_income,
        "filing_status": filing_status,
        "estimated_tax_owed": round(tax_owed, 2),
        "effective_tax_rate": round(effective_rate * 100, 2),
        "tax_year": TAX_YEAR,
        "disclaimer": (
            "This is a simplified, educational estimate only -- not tax advice. Does not "
            "account for credits, deductions beyond the standard deduction, state taxes, "
            "or other factors. Consult a licensed CPA or tax professional for an accurate "
            "calculation."
        ),
    }


def estimate_self_employment_tax(net_self_employment_income: float) -> Dict[str, Any]:
    """
    Rough educational estimate of self-employment tax owed. Simplified --
    does not account for the 92.35% net-earnings adjustment, deductions, or
    other real-world factors. Not tax advice.
    """
    # Standard simplification: SE tax applies to 92.35% of net SE income.
    se_taxable_income = net_self_employment_income * 0.9235
    ss_wage_base = SELF_EMPLOYMENT_TAX["social_security_wage_base_2025"]

    ss_taxable = min(se_taxable_income, ss_wage_base)
    ss_tax = ss_taxable * SELF_EMPLOYMENT_TAX["social_security_rate"]
    medicare_tax = se_taxable_income * SELF_EMPLOYMENT_TAX["medicare_rate"]

    total_se_tax = ss_tax + medicare_tax

    return {
        "net_self_employment_income": net_self_employment_income,
        "estimated_se_tax": round(total_se_tax, 2),
        "social_security_portion": round(ss_tax, 2),
        "medicare_portion": round(medicare_tax, 2),
        "tax_year": TAX_YEAR,
        "disclaimer": (
            "This is a simplified, educational estimate only -- not tax advice. Consult a "
            "licensed CPA or tax professional for an accurate calculation."
        ),
    }


def get_reference_summary() -> Dict[str, Any]:
    """Get a full reference summary for display (e.g. on an admin tax reference page)."""
    return {
        "tax_year": TAX_YEAR,
        "source_note": SOURCE_NOTE,
        "federal_brackets": FEDERAL_TAX_BRACKETS_2025,
        "standard_deductions": STANDARD_DEDUCTION_2025,
        "self_employment_tax": SELF_EMPLOYMENT_TAX,
        "standard_mileage_rate": STANDARD_MILEAGE_RATE_2025,
    }
