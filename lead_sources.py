"""
Legitimate lead data ingestion from public APIs and partner networks.
Supports: Google Places API, Census data, affiliate lead networks.
"""

import os
import logging
import httpx
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LeadSource(BaseModel):
    """Represents a single lead from an external source."""

    name: str
    email: str
    phone: str
    location: str
    service_category: str  # e.g., "home repair", "financial services"
    source_type: str  # "google_places", "census", "affiliate"
    source_name: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    raw_data: Optional[Dict[str, Any]] = None


class GooglePlacesLeadSource:
    """Fetch business leads from Google Places API by location and category."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://maps.googleapis.com/maps/api/place"

    async def search_by_location_and_category(
        self, location: str, category: str, radius: int = 5000
    ) -> List[LeadSource]:
        """
        Search for businesses in a location by category.
        Args:
            location: "latitude,longitude" or address string
            category: service type (e.g., "home repair", "electrician")
            radius: search radius in meters
        Returns:
            List of LeadSource objects
        """
        if not self.api_key:
            logger.warning("Google Places API key not configured")
            return []

        leads = []
        try:
            # First geocode the location if it's an address
            geo_lat_lng = await self._geocode(location)
            if not geo_lat_lng:
                return []

            # Search for nearby businesses
            async with httpx.AsyncClient(timeout=10.0) as client:
                search_url = f"{self.base_url}/nearbysearch/json"
                params = {
                    "location": geo_lat_lng,
                    "keyword": category,
                    "radius": radius,
                    "key": self.api_key,
                }

                resp = await client.get(search_url, params=params)
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") != "OK":
                    logger.warning(f"Google Places API error: {data.get('status')}")
                    return []

                for result in data.get("results", [])[:10]:  # Limit to 10 per request
                    leads.append(
                        LeadSource(
                            name=result.get("name", "Unknown"),
                            email=result.get("email") or f"{result.get('place_id')}@google-leads.local",
                            phone=result.get("formatted_phone_number", "N/A"),
                            location=result.get("formatted_address", location),
                            service_category=category,
                            source_type="google_places",
                            source_name="Google Local Search",
                            confidence_score=0.8,
                            raw_data=result,
                        )
                    )
        except Exception as e:
            logger.error(f"Google Places search error: {e}")

        return leads

    async def _geocode(self, location: str) -> Optional[str]:
        """Convert address string to lat,lng."""
        if "," in location:
            # Already in lat,lng format
            return location

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self.base_url}/geocode/json"
                params = {"address": location, "key": self.api_key}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") == "OK" and data.get("results"):
                    result = data["results"][0]
                    lat = result["geometry"]["location"]["lat"]
                    lng = result["geometry"]["location"]["lng"]
                    return f"{lat},{lng}"
        except Exception as e:
            logger.error(f"Geocoding error for '{location}': {e}")

        return None


class CensusLeadAnalyzer:
    """Analyze census data to identify high-opportunity geographic areas."""

    def __init__(self, census_api_key: str):
        self.api_key = census_api_key
        self.base_url = "https://api.census.gov/data"

    async def find_underserved_areas(
        self, state: str, min_income: int = 30000, max_income: int = 100000
    ) -> List[Dict[str, Any]]:
        """
        Find census tracts with high demand but underserved populations.
        Returns geographic hotspots for targeting ads/leads.
        """
        if not self.api_key:
            logger.warning("Census API key not configured")
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Example: Find median income by county
                url = f"{self.base_url}/2021/acs/acs5"
                params = {
                    "get": "NAME,B19013_001E",  # Median income
                    "for": f"county:*",
                    "in": f"state:{self._state_to_fips(state)}",
                    "key": self.api_key,
                }

                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                hotspots = []
                for row in data[1:]:  # Skip header
                    name, median_income, *location = row
                    income = int(median_income) if median_income and median_income != "null" else 0

                    if min_income <= income <= max_income:
                        hotspots.append(
                            {
                                "name": name,
                                "median_income": income,
                                "state_fips": location[1] if len(location) > 1 else state,
                                "county_fips": location[0] if location else None,
                                "opportunity_score": 0.75 + (0.25 * (income / 100000)),
                            }
                        )

                return sorted(hotspots, key=lambda x: x["opportunity_score"], reverse=True)[:20]
        except Exception as e:
            logger.error(f"Census analysis error: {e}")
            return []

    @staticmethod
    def _state_to_fips(state: str) -> str:
        """Convert state abbreviation to FIPS code."""
        fips_map = {
            "AL": "01",
            "AK": "02",
            "AZ": "04",
            "AR": "05",
            "CA": "06",
            "CO": "08",
            "CT": "09",
            "DE": "10",
            "FL": "12",
            "GA": "13",
            "HI": "15",
            "ID": "16",
            "IL": "17",
            "IN": "18",
            "IA": "19",
            "KS": "20",
            "KY": "21",
            "LA": "22",
            "ME": "23",
            "MD": "24",
            "MA": "25",
            "MI": "26",
            "MN": "27",
            "MS": "28",
            "MO": "29",
            "MT": "30",
            "NE": "31",
            "NV": "32",
            "NH": "33",
            "NJ": "34",
            "NM": "35",
            "NY": "36",
            "NC": "37",
            "ND": "38",
            "OH": "39",
            "OK": "40",
            "OR": "41",
            "PA": "42",
            "RI": "44",
            "SC": "45",
            "SD": "46",
            "TN": "47",
            "TX": "48",
            "UT": "49",
            "VT": "50",
            "VA": "51",
            "WA": "53",
            "WV": "54",
            "WI": "55",
            "WY": "56",
        }
        return fips_map.get(state.upper(), "06")  # Default to CA


class AffiliateLeadNetwork:
    """Fetch and manage leads from affiliate partner networks."""

    def __init__(self, partner_configs: List[Dict[str, str]]):
        """
        Args:
            partner_configs: List of dicts with 'name', 'api_url', 'api_key'
        """
        self.partners = partner_configs

    async def fetch_available_leads(self) -> List[LeadSource]:
        """Fetch leads from all configured affiliate partners."""
        all_leads = []

        for partner in self.partners:
            try:
                leads = await self._fetch_from_partner(partner)
                all_leads.extend(leads)
            except Exception as e:
                logger.error(f"Error fetching from affiliate {partner.get('name')}: {e}")

        return all_leads

    async def _fetch_from_partner(self, partner: Dict[str, str]) -> List[LeadSource]:
        """Fetch leads from a single partner API."""
        if not all(k in partner for k in ["name", "api_url", "api_key"]):
            logger.warning(f"Incomplete partner config: {partner}")
            return []

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {"Authorization": f"Bearer {partner['api_key']}"}
                resp = await client.get(partner["api_url"], headers=headers)
                resp.raise_for_status()
                data = resp.json()

                leads = []
                for item in data.get("leads", []):
                    leads.append(
                        LeadSource(
                            name=item.get("name", "Unknown"),
                            email=item.get("email", ""),
                            phone=item.get("phone", ""),
                            location=item.get("location", ""),
                            service_category=item.get("category", ""),
                            source_type="affiliate",
                            source_name=partner["name"],
                            confidence_score=0.9,
                            raw_data=item,
                        )
                    )
                return leads
        except Exception as e:
            logger.error(f"Partner API error ({partner['name']}): {e}")
            return []


def store_leads_to_db(conn: sqlite3.Connection, leads: List[LeadSource]) -> int:
    """Store fetched leads into the database. Returns count inserted."""
    cursor = conn.cursor()
    count = 0

    for lead in leads:
        try:
            # Check if lead already exists (by email + phone)
            existing = cursor.execute(
                "SELECT id FROM leads WHERE email = ? AND phone = ?",
                (lead.email, lead.phone),
            ).fetchone()

            if not existing:
                cursor.execute(
                    """
                    INSERT INTO leads (
                        full_name, email, phone, application_type, requested_product,
                        source, consent_text, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lead.name,
                        lead.email,
                        lead.phone,
                        "business",
                        lead.service_category,
                        f"{lead.source_type}:{lead.source_name}",
                        f"Auto-discovered from {lead.source_name} - Confidence: {lead.confidence_score}",
                        "discovered",
                    ),
                )
                count += 1
        except sqlite3.IntegrityError:
            pass  # Duplicate or constraint violation

    conn.commit()
    logger.info(f"Stored {count} new leads to database")
    return count
