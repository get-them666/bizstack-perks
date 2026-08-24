import sqlite3

DATABASE_PATH = "data/bizstack.db"

def scout_and_ingest(first_name, last_name, email, phone, card_type, source_type):
    """Saves targeted corporate entities and branch scouts to the card_leads matrix."""
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO card_leads (first_name, last_name, email, phone, card_type, status)
            VALUES (?, ?, ?, ?, ?, 'PENDING')
            """,
            (first_name, last_name, email, phone, card_type)
        )
        conn.commit()
        print(f"📡 [SCOUT SUCCESS] Ingested {source_type}: {email} ({card_type})")
    except sqlite3.IntegrityError:
        print(f"⏭️  [SKIPPED] {email} already tracking in system matrix.")
    finally:
        conn.close()

def run_scout_loop():
    print("🚀 Booting terminal scout engine...")
    
    # Scouted datasets representing standard B2B companies and regional banks
    scouted_entities = [
        {
            "first": "Managing", "last": "Director", "email": "treasury@metro-com-bank.com", 
            "phone": "+15554441122", "card_type": "Bank Node Referral", "type": "BANK BRANCH"
        },
        {
            "first": "Operations", "last": "Lead", "email": "hq@apex-logistics.com", 
            "phone": "+15559998877", "card_type": "Mastercard Fleet Business", "type": "LOCAL BUSINESS"
        }
    ]
    
    for entity in scouted_entities:
        scout_and_ingest(
            entity["first"], entity["last"], entity["email"], 
            entity["phone"], entity["card_type"], entity["type"]
        )

if __name__ == "__main__":
    run_scout_loop()
