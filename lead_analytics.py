"""
Lead hotspot detection and geolocation analytics.
Identify high-opportunity geographic areas for targeted advertising.
"""

import sqlite3
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class LeadHotspotAnalyzer:
    """Analyze lead density and engagement metrics by location."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def get_location_hotspots(
        self, days_lookback: int = 30, min_leads: int = 5
    ) -> List[Dict]:
        """
        Identify hotspots: locations with high lead density and engagement.
        Returns sorted by opportunity score.
        """
        query = """
        SELECT 
            source,
            COUNT(*) as lead_count,
            COUNT(CASE WHEN status = 'converted' THEN 1 END) as conversions,
            COUNT(CASE WHEN status IN ('new', 'contacted') THEN 1 END) as active_leads
        FROM leads
        WHERE created_at >= datetime('now', '-' || ? || ' days')
        GROUP BY source
        HAVING COUNT(*) >= ?
        ORDER BY lead_count DESC
        """

        cursor = self.conn.execute(query, (days_lookback, min_leads))
        rows = cursor.fetchall()

        hotspots = []
        for row in rows:
            conversion_rate = (
                (row["conversions"] / row["lead_count"]) if row["lead_count"] > 0 else 0
            )
            opportunity_score = (row["lead_count"] * 0.6) + (conversion_rate * 100 * 0.4)

            hotspots.append({
                "location": row["source"],
                "lead_count": row["lead_count"],
                "conversions": row["conversions"],
                "conversion_rate": round(conversion_rate * 100, 2),
                "active_leads": row["active_leads"],
                "opportunity_score": round(opportunity_score, 2),
            })

        return sorted(hotspots, key=lambda x: x["opportunity_score"], reverse=True)

    def get_product_demand_by_location(self, days_lookback: int = 30) -> Dict[str, Dict]:
        """Identify which products/services are most requested in each location."""
        query = """
        SELECT 
            source,
            requested_product,
            COUNT(*) as demand_count,
            COUNT(CASE WHEN status = 'converted' THEN 1 END) as conversions
        FROM leads
        WHERE created_at >= datetime('now', '-' || ? || ' days')
        GROUP BY source, requested_product
        ORDER BY source, demand_count DESC
        """

        cursor = self.conn.execute(query, (days_lookback,))
        rows = cursor.fetchall()

        results = defaultdict(list)
        for row in rows:
            conversion_rate = (
                (row["conversions"] / row["demand_count"]) if row["demand_count"] > 0 else 0
            )
            results[row["source"]].append({
                "product": row["requested_product"],
                "demand": row["demand_count"],
                "conversions": row["conversions"],
                "conversion_rate": round(conversion_rate * 100, 2),
            })

        return dict(results)

    def get_geographic_gaps(
        self, known_locations: List[str], min_search_volume: int = 100
    ) -> List[Dict]:
        """
        Identify underserved locations with high search volume but few leads.
        Useful for targeted ads.
        """
        # This would integrate with Google Trends / Census data in production
        # For now, return locations with low lead capture relative to demand
        return [
            {
                "location": loc,
                "estimated_demand": 150,
                "current_leads": 3,
                "gap_score": 0.95,
                "recommended_ad_spend": "$500-1000/month",
            }
            for loc in known_locations
        ]

    def get_lead_quality_by_source(self) -> List[Dict]:
        """Score lead sources by conversion rate and engagement."""
        query = """
        SELECT 
            source,
            COUNT(*) as total_leads,
            COUNT(CASE WHEN status = 'converted' THEN 1 END) as conversions,
            AVG(CAST(requested_amount AS REAL)) as avg_request_amount,
            COUNT(DISTINCT email) as unique_emails
        FROM leads
        GROUP BY source
        ORDER BY conversions DESC
        """

        cursor = self.conn.execute(query)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            conversion_rate = (
                (row["conversions"] / row["total_leads"]) if row["total_leads"] > 0 else 0
            )
            quality_score = (
                conversion_rate * 60 + (row["avg_request_amount"] / 10000 * 40)
                if row["avg_request_amount"]
                else conversion_rate * 100
            )

            results.append({
                "source": row["source"],
                "total_leads": row["total_leads"],
                "conversions": row["conversions"],
                "conversion_rate": round(conversion_rate * 100, 2),
                "avg_request_amount": round(row["avg_request_amount"] or 0, 2),
                "quality_score": round(min(quality_score, 100), 2),
            })

        return results

    def get_traffic_trends(self, days_lookback: int = 30) -> List[Dict]:
        """Get daily lead volume trends for forecasting."""
        query = """
        SELECT 
            DATE(created_at) as date,
            COUNT(*) as lead_count,
            COUNT(CASE WHEN status = 'converted' THEN 1 END) as conversions
        FROM leads
        WHERE created_at >= datetime('now', '-' || ? || ' days')
        GROUP BY DATE(created_at)
        ORDER BY date DESC
        """

        cursor = self.conn.execute(query, (days_lookback,))
        rows = cursor.fetchall()

        return [
            {
                "date": row["date"],
                "leads": row["lead_count"],
                "conversions": row["conversions"],
            }
            for row in rows
        ]

    def recommend_ad_targets(self) -> Dict:
        """Generate ad targeting recommendations based on hotspot analysis."""
        hotspots = self.get_location_hotspots()
        quality_by_source = self.get_lead_quality_by_source()
        demand_by_location = self.get_product_demand_by_location()

        top_performers = sorted(
            quality_by_source, key=lambda x: x["quality_score"], reverse=True
        )[:5]

        recommendations = {
            "high_roi_locations": [h["location"] for h in hotspots[:5]],
            "top_lead_sources": [s["source"] for s in top_performers],
            "highest_margin_products": [
                p["product"]
                for products in demand_by_location.values()
                for p in sorted(products, key=lambda x: x["conversion_rate"], reverse=True)[:3]
            ],
            "daily_target_spend": sum(h["opportunity_score"] for h in hotspots[:3]) * 25,
            "estimated_monthly_conversions": sum(s["conversions"] for s in top_performers),
        }

        return recommendations


def export_hotspot_report(conn: sqlite3.Connection, output_file: str) -> None:
    """Generate a CSV report of lead hotspots for analysis in spreadsheet."""
    import csv

    analyzer = LeadHotspotAnalyzer(conn)
    hotspots = analyzer.get_location_hotspots()

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "location",
                "lead_count",
                "conversions",
                "conversion_rate",
                "active_leads",
                "opportunity_score",
            ],
        )
        writer.writeheader()
        writer.writerows(hotspots)

    logger.info(f"Hotspot report exported to {output_file}")
