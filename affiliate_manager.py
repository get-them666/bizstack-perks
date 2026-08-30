"""
Affiliate commission tracking and payout management.
Track referrals, commissions, and partner payouts.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from enum import Enum
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CommissionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PAID = "paid"
    DISPUTED = "disputed"


class AffiliatePartner(BaseModel):
    """Represents an affiliate partner."""

    id: Optional[int] = None
    name: str
    contact_email: str
    commission_percentage: float = Field(ge=0.0, le=100.0)
    payout_method: str  # "stripe", "paypal", "bank_transfer", "check"
    payout_account: str  # Account identifier (email, account ID, etc.)
    is_active: bool = True


class AffiliateCommission(BaseModel):
    """Represents a single commission earned."""

    id: Optional[int] = None
    partner_id: int
    lead_id: int
    conversion_value: float
    commission_amount: float
    status: CommissionStatus = CommissionStatus.PENDING
    payout_date: Optional[str] = None


class AffiliateCommissionManager:
    """Manage affiliate partners and commission tracking."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        """Initialize affiliate-related database tables."""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS affiliate_partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                contact_email TEXT NOT NULL,
                commission_percentage REAL NOT NULL,
                payout_method TEXT NOT NULL,
                payout_account TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS affiliate_commissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id INTEGER NOT NULL,
                lead_id INTEGER NOT NULL,
                conversion_value REAL NOT NULL,
                commission_amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                payout_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (partner_id) REFERENCES affiliate_partners(id),
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
            """
        )

        self.conn.commit()

    def add_partner(self, partner: AffiliatePartner) -> int:
        """Add a new affiliate partner. Returns partner ID."""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            INSERT INTO affiliate_partners (
                name, contact_email, commission_percentage, payout_method, payout_account
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                partner.name,
                partner.contact_email,
                partner.commission_percentage,
                partner.payout_method,
                partner.payout_account,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_partner(self, partner_id: int) -> Optional[Dict]:
        """Fetch a partner by ID."""
        cursor = self.conn.execute(
            "SELECT * FROM affiliate_partners WHERE id = ?", (partner_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_active_partners(self) -> List[Dict]:
        """Get all active affiliate partners."""
        cursor = self.conn.execute(
            "SELECT * FROM affiliate_partners WHERE is_active = 1 ORDER BY name"
        )
        return [dict(row) for row in cursor.fetchall()]

    def record_commission(
        self, partner_id: int, lead_id: int, conversion_value: float
    ) -> int:
        """
        Record a new commission when a lead converts.
        Returns commission ID.
        """
        partner = self.get_partner(partner_id)
        if not partner:
            raise ValueError(f"Partner {partner_id} not found")

        commission_amount = conversion_value * (partner["commission_percentage"] / 100)

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO affiliate_commissions (
                partner_id, lead_id, conversion_value, commission_amount, status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (partner_id, lead_id, conversion_value, commission_amount, CommissionStatus.PENDING),
        )
        self.conn.commit()

        logger.info(
            f"Commission recorded: partner={partner_id}, lead={lead_id}, amount=${commission_amount:.2f}"
        )
        return cursor.lastrowid

    def approve_commission(self, commission_id: int) -> None:
        """Approve a pending commission."""
        self.conn.execute(
            "UPDATE affiliate_commissions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (CommissionStatus.APPROVED, commission_id),
        )
        self.conn.commit()

    def mark_commission_paid(self, commission_id: int, payout_date: Optional[str] = None) -> None:
        """Mark a commission as paid."""
        if payout_date is None:
            payout_date = datetime.now().isoformat()

        self.conn.execute(
            """
            UPDATE affiliate_commissions 
            SET status = ?, payout_date = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
            """,
            (CommissionStatus.PAID, payout_date, commission_id),
        )
        self.conn.commit()

    def get_partner_earnings(self, partner_id: int, days_lookback: Optional[int] = None) -> Dict:
        """Get earnings summary for a partner."""
        if days_lookback:
            date_filter = f" AND created_at >= datetime('now', '-{days_lookback} days')"
        else:
            date_filter = ""

        cursor = self.conn.execute(
            f"""
            SELECT 
                SUM(commission_amount) as total_earned,
                COUNT(*) as total_commissions,
                SUM(CASE WHEN status = 'paid' THEN commission_amount ELSE 0 END) as paid,
                SUM(CASE WHEN status = 'pending' THEN commission_amount ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'approved' THEN commission_amount ELSE 0 END) as approved
            FROM affiliate_commissions
            WHERE partner_id = ? {date_filter}
            """,
            (partner_id,),
        )

        row = cursor.fetchone()
        return {
            "total_earned": row[0] or 0.0,
            "total_commissions": row[1] or 0,
            "paid": row[2] or 0.0,
            "pending": row[3] or 0.0,
            "approved": row[4] or 0.0,
        }

    def get_pending_payouts(self, min_amount: float = 50.0) -> List[Dict]:
        """Get all partners with pending payouts over minimum threshold."""
        cursor = self.conn.execute(
            """
            SELECT 
                ap.id,
                ap.name,
                ap.contact_email,
                ap.payout_method,
                ap.payout_account,
                SUM(ac.commission_amount) as total_pending
            FROM affiliate_partners ap
            LEFT JOIN affiliate_commissions ac ON ap.id = ac.partner_id
            WHERE ac.status IN ('pending', 'approved')
            GROUP BY ap.id
            HAVING SUM(ac.commission_amount) >= ?
            ORDER BY total_pending DESC
            """,
            (min_amount,),
        )

        return [dict(row) for row in cursor.fetchall()]

    def generate_payout_batch(
        self, min_amount: float = 50.0
    ) -> Dict:
        """Generate a batch of payouts for pending commissions."""
        payouts = self.get_pending_payouts(min_amount)

        batch = {
            "batch_id": datetime.now().isoformat(),
            "total_amount": sum(p["total_pending"] for p in payouts),
            "partner_count": len(payouts),
            "payouts": payouts,
        }

        logger.info(
            f"Payout batch created: {batch['partner_count']} partners, ${batch['total_amount']:.2f}"
        )

        return batch

    def record_payout(self, commission_ids: List[int], payout_txn_id: str) -> None:
        """Mark a batch of commissions as paid with transaction ID."""
        placeholders = ",".join("?" * len(commission_ids))
        self.conn.execute(
            f"""
            UPDATE affiliate_commissions 
            SET status = ?, payout_date = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP 
            WHERE id IN ({placeholders})
            """,
            [CommissionStatus.PAID] + commission_ids,
        )
        self.conn.commit()

        logger.info(f"Payout recorded: txn_id={payout_txn_id}, commissions={len(commission_ids)}")


def calculate_referral_bonus(
    base_lead_value: float, affiliate_tier: str = "standard"
) -> Dict:
    """Calculate referral bonuses based on tier."""
    tiers = {
        "bronze": 0.05,  # 5%
        "silver": 0.10,  # 10%
        "gold": 0.15,  # 15%
        "platinum": 0.20,  # 20%
    }

    percentage = tiers.get(affiliate_tier, 0.05)
    commission = base_lead_value * percentage

    return {
        "tier": affiliate_tier,
        "percentage": percentage * 100,
        "base_value": base_lead_value,
        "commission": commission,
    }
