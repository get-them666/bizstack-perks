# ⚡ BIZSTACK PERKS UNIFIED OPERATIONAL NODE

A secure, high-performance production platform built with **FastAPI**, **SQLite**, and **Twilio Voice Integration**. Designed with a high-contrast, premium carbon-black matrix layout, this system processes commercial ingestion details, aggregates financial ledger metrics, maintains strict authorization access states, and manages dynamic telephony automation channels.

---

## 📊 Core Features

*   **Cyberpunk Visual Interface:** Hardened frontend layout system featuring dark UI telemetry grid structures, live search sorting engines, real-time UTC network clocks, and clean data submission pipelines.
*   **Secure Session Authentication:** Crypto-hardened portal walls using secure administrative tracking cookies configured with advanced web safety settings (`HttpOnly`, `Secure`, `SameSite=Lax`).
*   **Dynamic Telephony Core:** Real-time inbound webhook engines utilizing TwiML instruction matrices to intercept calls, process automated keypad responses, log events to database nodes, and trigger outbound broadcast loops.
*   **Data Integrity & Automation:** Automated asynchronous backend lifecycle scheduler that backs up database instances every 24 hours, alongside integrated CSV report generation, manual database download pathways, and custom HTML error deck fallbacks.
*   **Server-Side Boundary Guards:** Input verification limits protecting local endpoints from injection vulnerabilities or text block overflow spam.

---

## 📂 File Architecture Matrix

```text
bizstack-perks/
├── agent_prompts/
│   └── calling_rules.txt     # System vocal scripts & business rules
├── templates/
│   ├── index.html            # Dark-themed onboarding form gateway
│   ├── login.html            # Secure gateway entry screen
│   ├── dashboard.html        # Matrix table monitoring workspace
│   └── error.html            # Custom cyber-themed exception fallback page
├── main.py                   # Central ASGI runtime backend engine
├── requirements.txt          # Production application library dependencies
├── Procfile                  # Cloud infrastructure build instruction command
└── .gitignore                # Restricts local database logs & keys from escaping
```

---

## 🚀 Rapid Local Deployment

### 1. Initialize Your Environment & Dependencies
Ensure you are inside your virtual environment, then execute:
```bash
pip install -r requirements.txt
```

### 2. Configure Local Environment Tokens
```bash
export BIZSTACK_ADMIN_USER="admin"
export BIZSTACK_ADMIN_PASS="password123"
export TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TWILIO_AUTH_TOKEN="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TWILIO_NUMBER="+15550000000"
export BOT_API_TOKEN="use-a-long-random-value"
```

### 3. Initialize the Database Registry Matrix
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('data/bizstack.db')
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, company_name TEXT UNIQUE NOT NULL, credit_risk_rating TEXT, annual_revenue REAL)')
c.execute('CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_name TEXT, amount REAL, status TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
conn.commit()
conn.close()
"
```

### 4. Boot Up the ASGI Track Engine
```bash
uvicorn main:app --reload --port 8000
```
Access the application endpoint terminal via your browser at `http://127.0.0.1:8000`.

---

## 🌐 Live Production Provisioning (Railway + Cloudflare)

1.  **Code Push:** Ensure `.gitignore` is active, then execute `git push origin main`.
2.  **Railway Deploy:** Link your repository. In the **Variables** settings panel, map your custom production environment keys (`TWILIO_ACCOUNT_SID`, `BIZSTACK_ADMIN_PASS`, etc.).
3.  **Data Volume Mount:** Create a Railway persistent disk storage **Volume** component. Mount it at `/app/data` (not `/app/data/bizstack.db`) so SQLite can create the database file and preserve it across redeploys.
4.  **Cloudflare DNS Routing:** Map a `CNAME` record pointing your custom domain (`bizstackperks.com`) straight to your Railway platform domain with proxy active (orange cloud) to enable automated SSL protection layers.

---
© 2026 BizStack Perks LLC. Safe local ledger infrastructure operational.
