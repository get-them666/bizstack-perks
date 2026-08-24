import sqlite3

DATABASE_PATH = "data/bizstack.db"

def inject_real_lead(first_name, last_name, email, phone, card_type):
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
        print(f"✅ Ingested real prospect: {email}")
    except sqlite3.IntegrityError:
        print(f"⏭️ Skipping {email} - This corporate profile is already tracking in your database.")
    finally:
        conn.close()

if __name__ == "__main__":
    print("📈 Injecting production lead inventory data...")
    
    # 💡 REPLACE THESE WITH YOUR ACTUAL PROSPECT DETAILS
    real_leads_dataset = [
        {
            "first": "John", 
            "last": "Doe", 
            "email": "j.doe@mycompany.com", 
            "phone": "+12125550199", 
            "card": "Visa Business Cash Back"
        },
        {
            "first": "Alice", 
            "last": "Smith", 
            "email": "finance@regionalbank.corp", 
            "phone": "+13125550144", 
            "card": "Amex Corporate Gold"
        }
    ]
    
    for lead in real_leads_dataset:
        inject_real_lead(lead["first"], lead["last"], lead["email"], lead["phone"], lead["card"])
