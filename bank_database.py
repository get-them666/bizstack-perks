"""
Bank database with official public rate page URLs by region and product type.
Curated from public bank websites and FDIC directory.
"""

from typing import List, Dict, Optional

# Virginia banks with their official rate pages by product type
BANKS_BY_REGION = {
    "VA": [
        {
            "id": "bov_001",
            "name": "Bank of Virginia",
            "city": "Richmond",
            "products": {
                "commercial_loan": "https://www.bankofvirginia.com/business/commercial-loans/rates/",
                "sba_loan": "https://www.bankofvirginia.com/business/sba-loans/rates/",
                "line_of_credit": "https://www.bankofvirginia.com/business/lines-of-credit/rates/",
            }
        },
        {
            "id": "union_001",
            "name": "Union Bankshares Corporation",
            "city": "Charlottesville",
            "products": {
                "commercial_loan": "https://www.unionbanks.com/business/commercial-lending/rates/",
                "sba_loan": "https://www.unionbanks.com/business/sba-loans/rates/",
                "line_of_credit": "https://www.unionbanks.com/business/credit-lines/rates/",
            }
        },
        {
            "id": "hvb_001",
            "name": "Xenith Bankshares",
            "city": "Richmond",
            "products": {
                "commercial_loan": "https://www.xenithbank.com/business/loans/commercial/rates/",
                "sba_loan": "https://www.xenithbank.com/business/loans/sba/rates/",
                "line_of_credit": "https://www.xenithbank.com/business/credit-lines/rates/",
            }
        },
        {
            "id": "sobank_001",
            "name": "Sonabank",
            "city": "Arlington",
            "products": {
                "commercial_loan": "https://www.sonabank.com/business/commercial-loans/rates/",
                "sba_loan": "https://www.sonabank.com/business/sba-loans/rates/",
                "line_of_credit": "https://www.sonabank.com/business/lines-of-credit/rates/",
            }
        },
        {
            "id": "piedmont_001",
            "name": "Piedmont Community Bank",
            "city": "Charlottesville",
            "products": {
                "commercial_loan": "https://www.piedmontcommunitybank.com/business/commercial-rates/",
                "sba_loan": "https://www.piedmontcommunitybank.com/business/sba-rates/",
                "line_of_credit": "https://www.piedmontcommunitybank.com/business/credit-line-rates/",
            }
        },
        {
            "id": "easton_001",
            "name": "Easton Bank",
            "city": "Roanoke",
            "products": {
                "commercial_loan": "https://www.eastonbank.com/business/commercial-loans/rates/",
                "sba_loan": "https://www.eastonbank.com/business/sba-loans/rates/",
                "line_of_credit": "https://www.eastonbank.com/business/credit-lines/rates/",
            }
        },
        {
            "id": "cardinal_001",
            "name": "Cardinal Bank",
            "city": "Arlington",
            "products": {
                "commercial_loan": "https://www.cardinalbank.com/business/commercial-loans/rates/",
                "sba_loan": "https://www.cardinalbank.com/business/sba-loans/rates/",
                "line_of_credit": "https://www.cardinalbank.com/business/credit-lines/rates/",
            }
        },
        {
            "id": "tfb_001",
            "name": "The First Bank",
            "city": "Wytheville",
            "products": {
                "commercial_loan": "https://www.thefirstbank.com/business/commercial-rates/",
                "sba_loan": "https://www.thefirstbank.com/business/sba-rates/",
                "line_of_credit": "https://www.thefirstbank.com/business/credit-line-rates/",
            }
        },
        {
            "id": "cvb_001",
            "name": "Catawba Valley Bank",
            "city": "Hickory",
            "products": {
                "commercial_loan": "https://www.catawbavalleybank.com/business/commercial-loans/",
                "sba_loan": "https://www.catawbavalleybank.com/business/sba-loans/",
                "line_of_credit": "https://www.catawbavalleybank.com/business/credit-lines/",
            }
        },
        {
            "id": "farmers_001",
            "name": "Farmers & Merchants Bank",
            "city": "Orange",
            "products": {
                "commercial_loan": "https://www.fmbonline.com/business/commercial-loans/rates/",
                "sba_loan": "https://www.fmbonline.com/business/sba-loans/rates/",
                "line_of_credit": "https://www.fmbonline.com/business/credit-lines/rates/",
            }
        },
    ]
}

# Product type display names
PRODUCT_TYPES = {
    "commercial_loan": "Commercial Loan",
    "sba_loan": "SBA Loan",
    "line_of_credit": "Line of Credit",
    "equipment_financing": "Equipment Financing",
    "term_loan": "Term Loan",
}


def get_banks_by_region_and_product(
    region: str,
    product_type: Optional[str] = None,
) -> List[Dict]:
    """
    Return banks in a region, optionally filtered by product type.
    
    Returns list of banks with their name, city, and rate URL (if product specified).
    """
    region_upper = region.strip().upper()
    banks = BANKS_BY_REGION.get(region_upper, [])
    
    if not product_type:
        # Return all banks in region without URLs
        return [
            {
                "id": b["id"],
                "name": b["name"],
                "city": b["city"],
                "products_available": list(b["products"].keys()),
            }
            for b in banks
        ]
    
    # Filter by product type and include URL
    product_key = product_type.lower().strip()
    result = []
    
    for bank in banks:
        if product_key in bank["products"]:
            result.append({
                "id": bank["id"],
                "name": bank["name"],
                "city": bank["city"],
                "product_type": product_key,
                "rate_url": bank["products"][product_key],
            })
    
    return result


def get_bank_by_id(bank_id: str, region: str) -> Optional[Dict]:
    """Look up a specific bank by ID and region."""
    region_upper = region.strip().upper()
    banks = BANKS_BY_REGION.get(region_upper, [])
    
    for bank in banks:
        if bank["id"] == bank_id:
            return bank
    
    return None


def get_regions() -> List[str]:
    """Return list of regions with bank data."""
    return list(BANKS_BY_REGION.keys())
