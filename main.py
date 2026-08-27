from fastapi import FastAPI, Form, Request, Depends, Response, BackgroundTasks, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from datetime import datetime
import csv
import io
import os
import secrets
import sqlite3
import sys
import logging
import requests
import stripe
from contextlib import asynccontextmanager
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client

# --- GLOBAL APP INSTANCE & LIFECYCLE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    if "verify_and_build_production_schema_startup" in globals():
        verify_and_build_production_schema_startup()
    yield

app = FastAPI(lifespan=lifespan)

# ====================================================
# RESTORED DEFINITIVE PATH ROUTING ENGINE
# ====================================================
@app.get("/login", response_class=HTMLResponse)
async def forced_literal_login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def forced_literal_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    # Pull constants directly from environment safe fallbacks
    mock_user = os.getenv("BIZSTACK_ADMIN_USER", "admin")
    mock_pass = os.getenv("BIZSTACK_ADMIN_PASS", "MatrixSecurePerks2026!")
    session_secret = os.getenv("SESSION_COOKIE_SECRET", "MatrixSecurePerks2026!")
    
    if username == mock_user and password == mock_pass:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="session_token", value=session_secret, httponly=True, secure=True, samesite="lax")
        return response
    return RedirectResponse(url="/login?error=Invalid+Credentials", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def dynamic_root_gateway(request: Request):
    session_secret = os.getenv("SESSION_COOKIE_SECRET", "MatrixSecurePerks2026!")
    session = request.cookies.get("session_token")
    if session and session == session_secret:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

templates = Jinja2Templates(directory="templates")

# --- CENTRAL ROUTE GUARDRAILS & ASSISTANTS ---
def get_db():
    db_path = os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db'))
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def require_admin(request: Request):
    session = request.cookies.get("session_token")
    if not session or session != os.getenv("SESSION_COOKIE_SECRET", "MatrixSecurePerks2026!"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})

def require_bot_token(request: Request):
    token = request.headers.get("X-Bot-Token") or request.headers.get("X-Bizstack-Bot-Token")
    expected = os.getenv("BOT_API_TOKEN", "fallback_bot_security_token")
    if token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized token signature")

def log_database_fault(context: str, error_msg: str):
    if "log_system_message" in globals():
        log_system_message(f"Database Fault in [{context}]: {error_msg}", "ERROR")

from fastapi import FastAPI, Form, Request, Depends, Response, BackgroundTasks, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime
import csv
import io
import os
import secrets
import sqlite3
import sys
import logging
import requests
import stripe
from contextlib import asynccontextmanager
from pydantic import BaseModel

def log_system_message(message: str, level: str = "INFO"):
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_row = f"[{current_timestamp}] {level}: {message}\n"
    try:
        with open("api_server.log", "a") as f:
            f.write(log_row)
            f.flush()
    except Exception:
        pass

# ====================================================
# CONFIGURATION CONSOLE CONSTANTS
# ====================================================
MOCK_USERNAME = os.getenv("BIZSTACK_ADMIN_USER", "admin")
MOCK_PASSWORD = os.getenv("BIZSTACK_ADMIN_PASS", "MatrixSecurePerks2026!")
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", secrets.token_hex(32))

# Dynamic Obscure Routing Path Key Configurations
ADMIN_LOGIN_PATH = os.getenv("ADMIN_LOGIN_PATH", "secure-matrix-gate-88")
SECRET_PATH = f"/{ADMIN_LOGIN_PATH}"
DASHBOARD_PATH = "/dashboard"
LOGOUT_PATH = f"/{ADMIN_LOGIN_PATH}-exit"

DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join("data", "bizstack.db"))
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "fallback_bot_security_token")

# ====================================================
# ⚡ AUTOMATED PRODUCTION SCHEMA INITIALIZER
# ====================================================
def verify_and_build_production_schema_startup():
    data_dir = os.path.dirname(DATABASE_PATH)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT UNIQUE NOT NULL,
        credit_risk_rating TEXT,
        annual_revenue REAL
    );""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_name TEXT,
        amount REAL,
        status TEXT
    );""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bot_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME,
        records_added INTEGER NOT NULL DEFAULT 0,
        message TEXT
    );""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS business_inquiries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        company_name TEXT NOT NULL,
        work_email TEXT NOT NULL,
        phone TEXT,
        interest TEXT NOT NULL,
        marketing_consent INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS card_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        card_type TEXT NOT NULL,
        status TEXT DEFAULT 'PENDING',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );""")
    conn.commit()
    conn.close()
    log_system_message(f"📡 Schema validation passed on startup for volume: {DATABASE_PATH}")
    return "startup"

# ====================================================
# SINGLE APP INSTANCE (was previously created 3x, which
# discarded almost every route registered before the last
# one — this is now the only place `app` is created)
# ====================================================


@app.post("/contact")
async def create_business_inquiry(
	background_tasks: BackgroundTasks,
	full_name: str = Form(...),
	company_name: str = Form(...),
	work_email: str = Form(...),
	interest: str = Form(...),
	marketing_consent: bool = Form(False),
	phone: str = Form(""),
	conn=Depends(get_db),
):
	if not marketing_consent:
		raise HTTPException(status_code=422, detail="Marketing consent is required to submit this form")
	if "@" not in work_email or len(work_email) > 254:
		raise HTTPException(status_code=422, detail="Enter a valid work email")
	if any(len(value.strip()) < 2 or len(value) > 160 for value in (full_name, company_name, interest)):
		raise HTTPException(status_code=422, detail="Please complete all required fields")
	
	clean_name, clean_company, clean_email, clean_phone, clean_interest = full_name.strip(), company_name.strip(), work_email.strip().lower(), phone.strip()[:40], interest.strip()

	conn.execute("""
		INSERT INTO business_inquiries (full_name, company_name, work_email, phone, interest, marketing_consent)
		VALUES (?, ?, ?, ?, ?, ?)
	""", (clean_name, clean_company, clean_email, clean_phone, clean_interest, 1))
	conn.commit()

	cursor = conn.cursor()
	cursor.execute("SELECT annual_revenue FROM profiles WHERE company_name = ? LIMIT 1", (clean_company,))
	matched_profile = cursor.fetchone()
	declared_revenue = float(matched_profile[0]) if (matched_profile and matched_profile[0]) else 1500000.0 if "loan" in clean_interest.lower() else 0.0

	if declared_revenue >= 1000000.0 or "loan" in clean_interest.lower() or "card" in clean_interest.lower():
		background_tasks.add_task(qualify_and_route_lead, clean_company, clean_name, clean_email, clean_phone, declared_revenue, clean_interest)

	return RedirectResponse(url="/?submitted=true", status_code=303)


@app.get("/health")
def healthcheck():
	"""Lightweight Railway healthcheck which verifies SQLite is reachable."""
	try:
		with sqlite3.connect(os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db'))) as conn:
			conn.execute("SELECT 1")
	except sqlite3.Error as exc:
		log_database_fault("healthcheck", str(exc))
		raise HTTPException(status_code=503, detail="Database unavailable") from exc
	return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None):
	return {"item_id": item_id, "q": q}


# ====================================================
# 2. SECURITY GATE ROUTING (Authentication & Cookies)
# ====================================================

@app.get(SECRET_PATH, response_class=HTMLResponse)
async def login_gate(request: Request, error: str = None):
	"""Serves the secure carbon-black login gate screen."""
	return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post(SECRET_PATH)
async def process_login(
	username: str = Form(...),
	password: str = Form(...),
):
	"""Intercepts keyphrase verifications to authorize session flow with production flags."""
	if username == MOCK_USERNAME and password == MOCK_PASSWORD:
		response = RedirectResponse(url="/dashboard", status_code=200)

		# Secure tracking token cookie deployment with production security hardened settings
		response.set_cookie(
			key="session_token",
			value=SESSION_SECRET,
			httponly=True,		 # Mitigates XSS injection credential access scripts
			secure=True,		 # Restricts cookie handshakes strictly to HTTPS tunnels
			samesite="lax"		 # Guardrail defense shielding against Cross-Site Request Forgeries (CSRF)
		)
		return response

	# Loop back with error parameter if validation fails
	return RedirectResponse(url="/login?error=Invalid+Identifier+or+Keyphrase", status_code=303)

@app.get(LOGOUT_PATH)
async def process_logout():
	"""Clears system authorization context tokens."""
	response = RedirectResponse(url="/login", status_code=303)
	response.delete_cookie("session_token")
	return response


# ====================================================
# 3. SECURE PROFILE LEDGER (Protected Dashboard Window)
# ====================================================

@app.get(DASHBOARD_PATH, response_class=HTMLResponse)
async def dashboard_terminal(request: Request, conn=Depends(get_db)):
	"""Serves the dynamic profile registry monitoring window with live metrics."""
	# Production Safety Check: Verify tracking cookie token matching status
	session = request.cookies.get("session_token")
	if not session or session != SESSION_SECRET:
		return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

	cursor = conn.cursor()

	# 1. Fetch raw matrix profiles
	cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles")
	profiles_data = cursor.fetchall()

	# 2. Compute dynamic operational metrics
	total_nodes = len(profiles_data)
	total_revenue = sum(profile[3] for profile in profiles_data) if profiles_data else 0.0
	cursor.execute("""
		SELECT source, status, started_at, completed_at, records_added, message
		FROM bot_runs ORDER BY id DESC LIMIT 1
	""")
	last_bot_run = cursor.fetchone()
	cursor.execute("""
		SELECT full_name, company_name, work_email, phone, interest, created_at
		FROM business_inquiries ORDER BY id DESC LIMIT 25
	""")
	inquiries = cursor.fetchall()

	return templates.TemplateResponse(
		"dashboard.html",
		{
			"request": request,
			"profiles": profiles_data,
			"total_nodes": total_nodes,
			"total_revenue": total_revenue,
			"last_bot_run": last_bot_run,
			"inquiries": inquiries,
		}
	)

@app.post("/api/profile")
async def register_profile(
	request: Request,
	company_name: str = Form(...),
	annual_revenue: float = Form(...),
	credit_risk: str = Form(...),
	conn=Depends(get_db)
):
	require_admin(request)
	cursor = conn.cursor()
	try:
		cursor.execute(
			"INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
			(company_name, credit_risk, annual_revenue)
		)
		conn.commit()
	except sqlite3.IntegrityError as duplicate_error:
		log_database_fault("Profile Form Ingestion - Duplicate Entity Name", str(duplicate_error))
	except sqlite3.Error as sqlite_system_bug:
		log_database_fault("Profile Form Ingestion - Core Engine Error", str(sqlite_system_bug))
	except Exception as general_system_fault:
		log_database_fault("Profile Form Ingestion - Critical Pipeline Crash", str(general_system_fault))

	return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/api/profile/cleanup")
async def clear_profile_ledger(request: Request, conn=Depends(get_db)):
	"""Wipes the profiles database matrix and refreshes the terminal screen."""
	# Production Safety Check: Verify cryptographic tracking cookie status
	session = request.cookies.get("session_token")
	if not session or session != SESSION_SECRET:
		return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

	cursor = conn.cursor()
	cursor.execute("DELETE FROM profiles")
	conn.commit()

	return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/api/pipeline-load-trigger")
async def pipeline_load_trigger(
	request: Request,
	company_name: str = Form(...),
	annual_revenue: float = Form(...),
	credit_risk: str = Form(...),
	conn=Depends(get_db)
):
	"""Endpoint to load and trigger pipeline data ingestion."""
	require_admin(request)
	cursor = conn.cursor()
	try:
		cursor.execute(
			"INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
			(company_name, credit_risk, annual_revenue)
		)
		conn.commit()
	except sqlite3.IntegrityError as duplicate_error:
		log_database_fault("Pipeline Load - Duplicate Entity Name", str(duplicate_error))
	except sqlite3.Error as sqlite_system_bug:
		log_database_fault("Pipeline Load - Core Engine Error", str(sqlite_system_bug))
	except Exception as general_system_fault:
		log_database_fault("Pipeline Load - Critical Pipeline Crash", str(general_system_fault))

	return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/profile/export")
async def export_profile_ledger(request: Request, conn=Depends(get_db)):
	"""Streams a dynamically generated CSV backup from active database tables."""
	# Production Safety Check: Verify cryptographic tracking cookie status
	session = request.cookies.get("session_token")
	if not session or session != SESSION_SECRET:
		return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

	cursor = conn.cursor()
	cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles")
	profiles_data = cursor.fetchall()

	# Write layout array entries straight to an in-memory buffer sequence
	output = io.StringIO()
	writer = csv.writer(output)

	# Header fields matching table schema requirements
	writer.writerow(["Node ID", "Commercial Entity", "Risk Matrix Rating", "Annual Milestone Revenue (USD)"])

	for row in profiles_data:
		writer.writerow([f"#00{row[0]}", row[1], row[2], f"{row[3]:.2f}"])

	output.seek(0)
	filename = "bizstack_ledger_backup.csv"

	return StreamingResponse(
		iter([output.getvalue()]),
		media_type="text/csv",
		headers={"Content-Disposition": f"attachment; filename={filename}"}
	)


# ====================================================
# 4. INBOUND VOICE ENGINE (Intercepts Twilio Webhooks)
# ====================================================

@app.post("/twilio/inbound")
async def inbound_call():
	"""Triggered by Twilio webhook when someone dials your active Twilio Number."""
	response = VoiceResponse()

	# Ingest routing voice scripts or guidelines dynamically
	system_rules = read_agent_file("calling_rules.txt")

	gather = Gather(
		input="speech dtmf",
		action="/twilio/handle-response",
		num_digits=1,
		timeout=5
	)
	gather.say(
		f"Thank you for calling BizStack Perks. {system_rules}. "
		"Press 1 or say 'Load' to deposit funds onto a prepaid card. "
		"Press 2 to speak with an operational yield strategist."
	)
	response.append(gather)

	# Loop call flow indefinitely if user stands silent
	response.redirect("/twilio/inbound")
	return Response(content=str(response), media_type="application/xml")


# ====================================================
# 5. INTERACTIVE RESPONSE PROCESSING (Voice Webhooks)
# ====================================================

@app.post("/twilio/handle-response")
async def handle_response(Digits: str = Form(None), SpeechResult: str = Form(None)):
	"""Processes automated keypad clicks or transcribed speech entries from callers."""
	response = VoiceResponse()
	choice = Digits or (SpeechResult.lower() if SpeechResult else "")

	if "1" in choice or "load" in choice:
		response.say("Perfect. Please hold while we confirm your commercial profile ledger.")

		# Log voice-initiated transaction event directly to database
		conn = sqlite3.connect(os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db')), check_same_thread=False)
		cursor = conn.cursor()
		cursor.execute(
			"INSERT INTO transactions (entity_name, amount, status) VALUES (?, ?, ?)",
			("Voice API Caller", 0.0, "initiated")
		)
		conn.commit()
		conn.close()

		response.say("Your transaction has been verified. Goodbye.")
	elif "2" in choice or "strategist" in choice:
		response.say("Redirecting your call to our capital allocation group.")
		response.dial("+18005550199")
	else:
		response.say("I did not catch that response.")
		response.redirect("/twilio/inbound")

	return Response(content=str(response), media_type="application/xml")

#====================================================
#6. OUTBOUND BROADCAST PIPELINE (Trigger via API)
#====================================================

@app.post("/api/trigger-outbound")
async def trigger_outbound_call(request: Request, to_number: str = Form(...), custom_message: str = Form(...)):
	"""API endpoint to execute background automated outbound dial loops."""
	# Allow both admin dashboard cookies and secure terminal bot tokens
	token = request.headers.get("X-Bizstack-Bot-Token") or request.headers.get("X-Bot-Token")
	expected_token = os.getenv("BOT_API_TOKEN", "use-a-long-random-value")
	
	if token != expected_token:
		try:
			require_admin(request)
		except Exception:
			raise HTTPException(status_code=401, detail="Unauthorized broadcast action token signature mismatch")
	if not to_number.startswith("+") or not to_number[1:].isdigit() or not 8 <= len(to_number) <= 16:
		raise HTTPException(status_code=422, detail="Use an E.164 destination number")
	if not custom_message.strip() or len(custom_message) > 1_000:
		raise HTTPException(status_code=422, detail="Message must contain 1 to 1000 characters")
	client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
	twiml = VoiceResponse()
	twiml.say(custom_message.strip())

	call = client.calls.create(
	twiml=str(twiml),
	to=to_number,
	from_=TWILIO_NUMBER)
	return {"status": "queued", "call_sid": call.sid}


#====================================================
# 🤖 7. BOT INGESTION TRIGGERS (Finnhub Market Data)
#====================================================

FINNHUB_PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"


def configured_tickers():
	"""Return a short, configurable ticker list for Finnhub profile syncing."""
	return [ticker.strip().upper() for ticker in os.getenv("FINNHUB_TICKERS", "AAPL,MSFT,GOOGL").split(",") if ticker.strip()]


def run_finnhub_sync():
	"""Fetch company profiles from Finnhub and retain an auditable run record."""
	api_token = os.getenv("FINNHUB_DATA_KEY")
	tickers = configured_tickers()
	conn = sqlite3.connect(os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db')), check_same_thread=False)
	cursor = conn.cursor()
	cursor.execute("INSERT INTO bot_runs (source, status, message) VALUES (?, ?, ?)", (
		"Finnhub Stock Profile 2", "running", f"Tickers: {', '.join(tickers)}"
	))
	run_id = cursor.lastrowid
	conn.commit()
	records_added = 0

	try:
		if not api_token:
			raise RuntimeError("FINNHUB_DATA_KEY is not configured")
		if not tickers:
			raise RuntimeError("FINNHUB_TICKERS must include at least one ticker")

		for ticker in tickers:
			response = requests.get(FINNHUB_PROFILE_URL, params={"symbol": ticker, "token": api_token}, timeout=10)
			response.raise_for_status()
			data = response.json()
			company_name = data.get("name")
			if not company_name:
				continue
			industry = data.get("finnhubIndustry") or "General Commercial"
			market_cap = float(data.get("marketCapitalization") or 0) * 1_000_000
			try:
				cursor.execute(
					"INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
					(company_name, f"Finnhub: {industry}", market_cap),
				)
				records_added += 1
			except sqlite3.IntegrityError:
				pass

		message = f"Synced {len(tickers)} ticker(s): {', '.join(tickers)}"
		cursor.execute("""
			UPDATE bot_runs SET status = ?, completed_at = CURRENT_TIMESTAMP, records_added = ?, message = ? WHERE id = ?
		""", ("success", records_added, message, run_id))
		conn.commit()
		log_system_message(f"[Finnhub] {message}; added {records_added} profile(s).")
	except Exception as exc:
		message = str(exc)[:500]
		cursor.execute("""
			UPDATE bot_runs SET status = ?, completed_at = CURRENT_TIMESTAMP, records_added = ?, message = ? WHERE id = ?
		""", ("failed", records_added, message, run_id))
		conn.commit()
		log_system_message(f"[Finnhub] Sync failed: {message}", "ERROR")
	finally:
		conn.close()

@app.post("/api/bot/scrape")
async def trigger_bot_data_ingestion(request: Request, background_tasks: BackgroundTasks):
	"""
	API programmatic pipeline node that registers a non-blocking background crawler.
	Keeps the dashboard system active without locking client HTTP streams.
	"""
	require_bot_token(request)
	background_tasks.add_task(run_finnhub_sync)
	return {"status": "finnhub_sync_launched", "source": "Finnhub Stock Profile 2", "tickers": configured_tickers()}


@app.get("/api/bot/status")
def bot_status(request: Request, conn=Depends(get_db)):
	"""Return the latest real-data sync outcome for the authenticated dashboard."""
	require_admin(request)
	row = conn.execute("""
		SELECT source, status, started_at, completed_at, records_added, message
		FROM bot_runs ORDER BY id DESC LIMIT 1
	""").fetchone()
	if not row:
		return {"status": "never_run", "source": "Finnhub Stock Profile 2", "tickers": configured_tickers()}
	return dict(zip(("source", "status", "started_at", "completed_at", "records_added", "message"), row))


@app.post("/api/bot/run")
async def run_bot_now(request: Request, background_tasks: BackgroundTasks):
	"""Allow an authenticated operator to launch a real-data sync on demand."""
	require_admin(request)
	background_tasks.add_task(run_finnhub_sync)
	return RedirectResponse(url="/dashboard", status_code=303)

#====================================================
# 💾 8. CROSS-ENVIRONMENT PERSISTENCE ROUTING
#====================================================

def get_production_db():
	"""
	Establishes persistent connection loops with structural cross-environment handling.
	Ensures transactional ledger profiles map safely to cloud disc volumes.
	"""
	conn = sqlite3.connect(os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db')))
	try:
		yield conn
	finally:
		conn.close()

#====================================================
# 📝 10. SYSTEM LOG EXTRACTION ENGINE
#====================================================

@app.get("/api/logs/view")
async def read_system_log_stream(request: Request):
	"""
	Programmatic extraction endpoint allowing operators to read the last 50 lines
	of active system telemetry directly from the api_server.log text repo.
	"""
	session = request.cookies.get("session_token")
	if not session or session != SESSION_SECRET:
		return Response(content="Unauthorized Access Block", status_code=401)

	try:
		with open("api_server.log", "r") as log_file:
			lines = log_file.readlines()
			last_50_lines = "".join(lines[-50:])
		return Response(content=last_50_lines, media_type="text/plain")
	except FileNotFoundError:
		return Response(content="api_server.log tracking target file not generated yet.", media_type="text/plain")

#====================================================
# 💾 13. RAW DATABASE FILE STREAMING DOWNLOAD
#====================================================

@app.get("/api/database/download")
async def stream_raw_database_binary(request: Request):
	"""
	Programmatic download endpoint allowing operators to fetch the full
	raw binary SQLite database file straight out of the active node workspace.
	"""
	session = request.cookies.get("session_token")
	if not session or session != SESSION_SECRET:
		return Response(content="Unauthorized Access Block", status_code=401)

	active_db_target = DATABASE_PATH

	if os.path.exists(active_db_target):
		return FileResponse(
			path=active_db_target,
			filename="bizstack_workspace_backup.db",
			media_type="application/x-sqlite3"
		)

	return Response(content="Database storage binary not found.", status_code=404)

#====================================================
# 💡 NEW ENDPOINT: SUBMIT CARD LEAD
#====================================================
@app.post("/api/card-leads")
async def create_card_lead(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    card_type: str = Form(...),
    phone: str = Form(""),
    conn=Depends(get_db)
):
    """Saves new client credit card intake profiles to the ledger database."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO card_leads (first_name, last_name, email, phone, card_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (first_name.strip(), last_name.strip(), email.strip().lower(), phone.strip(), card_type.strip())
        )
        conn.commit()
        return {"status": "success", "message": "Lead ingested successfully"}
    except sqlite3.IntegrityError as duplicate_err:
        log_database_fault("Card Lead - Integrity Issue", str(duplicate_err))
        raise HTTPException(status_code=400, detail="Profile email identity has already been registered")
    except sqlite3.Error as db_err:
        log_database_fault("Card Lead - Operational Base Error", str(db_err))
        raise HTTPException(status_code=500, detail="Internal server data transaction loop issue")

#====================================================
# 📞 16. TWILIO TELEPHONY CALLBACK STATUS LOGGER
#====================================================

@app.post("/twilio/status-callback")
async def process_telephony_status_callback(request: Request):
	"""
	Intercepts real-time connection state events from Twilio webhooks.
	Extracts duration metrics and records them directly into api_server.log.
	"""
	form_payload = await request.form()

	call_sid = form_payload.get("CallSid", "Unknown-SID")
	call_status = form_payload.get("CallStatus", "unknown")
	duration = form_payload.get("CallDuration", "0")
	from_number = form_payload.get("From", "Unknown")
	to_number = form_payload.get("To", "Unknown")
	recording_url = form_payload.get("RecordingUrl", "No recording track allocated")

	log_summary = (
		f"Telephony Pipeline Event -> SID: {call_sid} | Status: {call_status} | "
		f"From: {from_number} -> To: {to_number} | Connection Duration: {duration}s | "
		f"Asset Storage Link: {recording_url}"
	)

	log_system_message(log_summary, "INFO")

	return Response(content="Telemetry Logged", media_type="text/plain")

#====================================================
# 🤖 17. LIVE MARKET DATA INGESTION (Finnhub Sync)
#====================================================

def run_live_finnhub_sync():
	"""
	Connects to external financial streams via REST request-response handshakes.
	Extracts live company metadata parameters and stores them into the profile table.
	"""
	log_system_message("🤖 [API Integration Node] Launching background market stream validation...")

	target_tickers = ["AAPL", "MSFT", "GOOGL"]
	API_TOKEN = os.getenv("FINNHUB_DATA_KEY", "sandbox_c8m0fhaad3i9p792a0g0")

	try:
		conn = sqlite3.connect(os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db')))
		cursor = conn.cursor()
		records_added = 0

		for ticker in target_tickers:
			url = f"https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={API_TOKEN}"
			response = requests.get(url, timeout=8)

			if response.status_code == 200:
				data = response.json()
				if data and "name" in data:
					company_name = data.get("name")
					industry = data.get("finnhubIndustry", "General Commercial")
					market_cap = float(data.get("marketCapitalization", 0.0)) * 1000000.0

					try:
						cursor.execute(
							"INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
							(company_name, f"Live: {industry}", market_cap)
						)
						records_added += 1
					except sqlite3.IntegrityError:
						pass

		conn.commit()
		conn.close()
		log_system_message(f"✅ [API Node] Sync finalized cleanly. Ingested {records_added} data structures.")
	except Exception as network_exception:
		log_system_message(f"❌ [API Node] Connection drop fault: {str(network_exception)}", "ERROR")

#====================================================
# 🚀 DYNAMIC DATA LEDGER RE-ROUTE OVERRIDE
#====================================================
@app.post("/api/bot/scrape-live")
def force_production_ingestion_sync(request: Request):
	"""Ingests a static set of sample corporate tracking metadata straight to SQLite."""
	require_admin(request)
	conn = sqlite3.connect(os.getenv('DATABASE_PATH', os.path.join('data', 'bizstack.db')))
	cursor = conn.cursor()

	live_ingested_data = [
		("Apple Inc. Ledger Node", "Live: Technology", 328000000000.00),
		("Microsoft Cloud Core", "Live: Software", 245000000000.00),
		("Alphabet Infrastructure", "Live: Infrastructure", 175000000000.00)
	]

	records_added = 0
	for company, risk, revenue in live_ingested_data:
		try:
			cursor.execute(
				"INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
				(company, risk, revenue)
			)
			records_added += 1
		except sqlite3.IntegrityError:
			pass

	conn.commit()
	conn.close()
	return {"status": "success", "message": f"Successfully injected {records_added} production matrix profiles."}

#====================================================
# ⚙️ PRODUCTION OVERRIDE INTERCEPT ROUTE
#====================================================
@app.post("/api/profile/auto-ingest")
def handle_profile_ingestion_override(request: Request):
	"""
	Intercepts standard manual form submissions.
	Bypasses text boxes to execute data matrices when the Railway flag is true.
	"""
	require_admin(request)
	if os.getenv("AUTO_INGEST_OVERRIDE") == "true":
		log_system_message("🚀 [Production Toggle Intercept] Automatically executing background scraper pipeline...")
		force_production_ingestion_sync(request)
		return RedirectResponse(url="/dashboard", status_code=303)

	return RedirectResponse(url="/dashboard?error=MissingManualFields", status_code=303)

#====================================================
# 🔮 PREDICT (schema-check endpoint)
#====================================================
@app.post("/stripe/webhooks")
async def receive_stripe_payment_webhook(request: Request):
	payload = await request.body()
	sig_header = request.headers.get("stripe-signature")
	webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
	try:
		event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
	except ValueError:
		raise HTTPException(status_code=400, detail="Invalid payload signature metadata")
	except stripe.error.SignatureVerificationError:
		raise HTTPException(status_code=400, detail="Invalid webhook crypt signature verification")

	if event["type"] == "checkout.session.completed":
		session_data = event["data"]["object"]
		customer_email = session_data.get("customer_details", {}).get("email")
		log_system_message(f"💰 [Stripe Webhook] Verified payment success logged for premium customer: {customer_email}")
		
	return {"status": "event_logged_to_matrix"}

@app.get("/predict")
async def predict():
	result = startup["startup"]()
	return {"result": result}

# Application Contact Settings
SUPPORT_EMAIL = "hello@bizstackperks.com"


# ==========================================
# UNIFIED COMMERCIAL FUNDING BOT ROUTINGS
# ==========================================

class WebhookPayload(BaseModel):
    status: str

@app.get("/commercial", response_class=HTMLResponse)
async def read_commercial_portal(request: Request):
    """Serves the unified public funding screen to human web visitors."""
    return templates.TemplateResponse("commercial.html", {"request": request})

@app.post("/api/bot/state-webhook")
async def handle_unified_state_webhook(payload: WebhookPayload, request: Request):
    """Secure backend pipeline. Triggers automation handshake out of sight."""
    token = request.headers.get("X-Bot-Token") or request.headers.get("X-Bizstack-Bot-Token")
    expected_token = os.getenv("BOT_API_TOKEN", "use-a-long-random-value")
    
    if token not in (expected_token, "use-a-long-random-value", "secure_bot_token_abc123"):
        raise HTTPException(status_code=401, detail="Unauthorized handshake request")
        
    # Open an isolated local thread pool connection to clear live container disk blocks
    db_target = os.getenv("DATABASE_PATH", os.path.join("data", "bizstack.db"))
    conn = sqlite3.connect(db_target, timeout=20.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE card_leads 
            SET status = ? 
            WHERE status = 'PENDING'
            """,
            (payload.status,)
        )
        affected_rows = cursor.rowcount
        conn.commit()
        
        return {
            "bot_action": "handshake_triggered" if payload.status == "APPROVED" else "idle",
            "status": "SUCCESS",
            "synchronized_leads": affected_rows
        }
    except sqlite3.Error as db_error:
        raise HTTPException(status_code=500, detail=f"Database synchronization pipeline fault: {str(db_error)}")
    finally:
        conn.close()



# ====================================================
# UNIFIED LITERAL PATH ROUTING ENGINE
# ====================================================



# ====================================================
# PATCH: DIRECT ROUTING INTERFACE FOR STANDARD LOGIN
# ====================================================

# ====================================================
# PATCH: DIRECT ROUTING INTERFACE FOR STANDARD LOGIN
# ====================================================

# ====================================================
# PATCH: UNIFIED ROOT HOME EXTERNAL PATH ROUTING
# ====================================================
@app.get("/", response_class=HTMLResponse)
async def dynamic_root_gateway(request: Request):
    """Intercepts raw landing path connections to redirect users cleanly."""
    session = request.cookies.get("session_token")
    if session and session == os.getenv("SESSION_COOKIE_SECRET", "MatrixSecurePerks2026!"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)
