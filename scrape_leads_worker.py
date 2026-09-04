import sqlite3

DATABASE_PATH = "data/bizstack.db"

def inject_scraped_lead(first_name, last_name, email, phone, card_type="Visa Corporate"):
    """Inserts a newly parsed cold lead into the pending database ledger."""
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
        print(f"🎉 Ingested scraped lead record: {email}")
    except sqlite3.IntegrityError:
        print(f"⏭️ Skipping {email} - Profile already exists in ledger matrix.")
    finally:
        conn.close()

def run_spider():
    print("📡 Running background directory parsing loop...")
    # Mock data simulating parsed HTML structural outputs
    scraped_leads = [
        {"first": "John", "last": "Smith", "email": "john@smithtech.io", "phone": "+15559876543"},
        {"first": "Sarah", "last": "Connor", "email": "sarah@cyberdyne.co", "phone": "+15553210987"}
    ]
    for lead in scraped_leads:
        inject_scraped_lead(lead["first"], lead["last"], lead["email"], lead["phone"])

if __name__ == "__main__":
    run_spider()
