import sqlite3
import os

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join("data", "bizstack.db"))

def rank_high_value_leads():
    if not os.path.exists(DATABASE_PATH):
        print(f"❌ Database not found at {DATABASE_PATH}. Run your server or scrapers first.")
        return

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Query to cross-reference card leads with potential revenue data from profiles
    query = """
    SELECT 
        l.id, 
        l.first_name || ' ' || l.last_name AS contact_name, 
        l.email, 
        l.card_type, 
        l.status,
        COALESCE(p.annual_revenue, 0.0) AS annual_revenue,
        COALESCE(p.company_name, 'Independent Lead') AS company
    FROM card_leads l
    LEFT JOIN profiles p ON l.email LIKE '%' || LOWER(SUBSTR(p.company_name, 1, 5)) || '%' 
       OR LOWER(l.card_type) LIKE '%' || LOWER(p.company_name) || '%'
    """

    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        
        ranked_leads = []
        for row in rows:
            lead_id, name, email, card_type, status, revenue, company = row
            
            # Payout Logic Formula
            # 1. Base corporate card affiliate fee
            card_payout = 250.00 if status == "APPROVED" else 0.00
            
            # 2. Projected Loan Commission: 2% payout assuming loan value is 10% of annual revenue
            projected_loan = revenue * 0.10
            loan_commission = projected_loan * 0.02 if status == "APPROVED" else 0.00
            
            total_payout = card_payout + loan_commission
            
            ranked_leads.append({
                "id": lead_id,
                "name": name,
                "email": email,
                "company": company,
                "status": status,
                "revenue": revenue,
                "payout": total_payout
            })

        # Sort leads starting with the highest payout
        ranked_leads.sort(key=lambda x: x["payout"], reverse=True)

        print("\n=========================================================================")
        print("💰 BIZSTACK PERKS AFFILIATE LEDGER: POTENTIAL LEADS BY PAYOUT VALUE 💰")
        print("=========================================================================")
        print(f"{'ID':<4} | {'CONTACT NAME':<15} | {'COMPANY / EMAIL':<25} | {'STATUS':<9} | {'EST. PAYOUT'}")
        print("-------------------------------------------------------------------------")
        
        for lead in ranked_leads:
            display_source = lead["company"] if lead["company"] != "Independent Lead" else lead["email"]
            print(f"#{lead['id']:<3} | {lead['name']:<15} | {display_source:<25} | {lead['status']:<9} | ${lead['payout']:,.2f}")
        print("=========================================================================\n")

    except sqlite3.Error as e:
        print(f"❌ SQL Execution fault: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    rank_high_value_leads()
