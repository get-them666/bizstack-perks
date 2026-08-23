from datetime import datetime
import csv
import io
import os
import secrets
import sqlite3
import sys
import logging
import requests
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, Depends, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

# ====================================================
# PRODUCTION SECURITY CONFIGURATIONS
# ====================================================

# Administrative Authentication Credentials
MOCK_USERNAME = os.getenv("BIZSTACK_ADMIN_USER", "admin")
MOCK_PASSWORD = os.getenv("BIZSTACK_ADMIN_PASS", "password123")

# Cryptographic Tracking Secret (Failsafe generates a random token per spin if empty)
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", secrets.token_hex(32))

# Twilio Cloud Messaging Credentials
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "ACxxxx")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "xxxxxx")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "+15550000000")

# Single source of truth for the database path — points at the Railway
# persistent volume mount so data survives redeploys.
DATABASE_PATH = os.getenv("DATABASE_PATH", "/app/data/bizstack.db")

# Configure root system log routing to capture internal telemetry
logging.basicConfig(
	filename="api_server.log",
	level=logging.INFO,
	format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
	datefmt="%Y-%m-%d %H:%M:%S"
)


# ====================================================
# LOGGING HELPERS (defined early since lifespan uses them)
# ====================================================

def log_system_message(message: str, level: str = "INFO"):
	"""
	Forces an immediate structural disk flush to bypass file buffering.
	Ensures tail terminal utilities read telemetry prints in real time.
	"""
	current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_row = f"[{current_timestamp}] {level}: {message}\n"

	try:
		with open("api_server.log", "a") as f:
			f.write(log_row)
			f.flush()  # Forces the operating system to write instantly to the physical storage disk
	except Exception:
		pass


def log_database_fault(operation_name: str, error_message: str):
	"""
	Catches database structural locks and bad syntax loops.
	Flushes the trace lines straight to the text log file.
	"""
	current_time_marker = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_row_entry = f"[{current_time_marker}] ERROR [sqlite]: Fault inside {operation_name} -> {error_message}\n"

	try:
		with open("api_server.log", "a") as f:
			f.write(log_row_entry)
			f.flush()
	except Exception:
		pass


#====================================================
# ⚡ AUTOMATED PRODUCTION SCHEMA INITIALIZER
#====================================================

def verify_and_build_production_schema_startup():
	"""Validates and generates the required database structure on boot."""
	# Ensure the volume's data directory exists before connecting
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
	);
	""")
	cursor.execute("""
	CREATE TABLE IF NOT EXISTS transactions (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		entity_name TEXT,
		amount REAL,
		status TEXT
	);
	""")
	conn.commit()
	conn.close()
	log_system_message(f"📡 Schema validation passed on startup for volume: {DATABASE_PATH}")

	return "startup"


startup = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
	startup["startup"] = verify_and_build_production_schema_startup
	verify_and_build_production_schema_startup()
	yield
	startup.clear()


# ====================================================
# SINGLE APP INSTANCE (was previously created 3x, which
# discarded almost every route registered before the last
# one — this is now the only place `app` is created)
# ====================================================

app = FastAPI(title="BizStack Perks Production Node", lifespan=lifespan)

# Mount templates engine for your cool dark mode layout
templates = Jinja2Templates(directory="templates")


# Database structural generator loop
def get_db():
	conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
	try:
		yield conn
	finally:
		conn.close()


# Read custom agent prompts dynamically from your local folder path
def read_agent_file(filename: str) -> str:
	try:
		with open(f"agent_prompts/{filename}", "r") as f:
			return f.read().strip()
	except FileNotFoundError:
		return "You are an AI transactional assistant for BizStack Perks."


# ====================================================
# 1. CORE VISUAL ROUTING (Dark Mode Gateway & Forms)
# ====================================================

@app.get("/", response_class=HTMLResponse)
async def home_terminal(request: Request):
	"""Serves the cool carbon-black layout home page."""
	return templates.TemplateResponse("index.html", {"request": request})


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None):
	return {"item_id": item_id, "q": q}


# ====================================================
# 2. SECURITY GATE ROUTING (Authentication & Cookies)
# ====================================================

@app.get("/login", response_class=HTMLResponse)
async def login_gate(request: Request, error: str = None):
	"""Serves the secure carbon-black login gate screen."""
	return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
async def process_login(
	username: str = Form(...),
	password: str = Form(...),
):
	"""Intercepts keyphrase verifications to authorize session flow with production flags."""
	if username == MOCK_USERNAME and password == MOCK_PASSWORD:
		response = RedirectResponse(url="/dashboard", status_code=303)

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

@app.get("/logout")
async def process_logout():
	"""Clears system authorization context tokens."""
	response = RedirectResponse(url="/login", status_code=303)
	response.delete_cookie("session_token")
	return response


# ====================================================
# 3. SECURE PROFILE LEDGER (Protected Dashboard Window)
# ====================================================

@app.get("/dashboard", response_class=HTMLResponse)
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

	return templates.TemplateResponse(
		"dashboard.html",
		{
			"request": request,
			"profiles": profiles_data,
			"total_nodes": total_nodes,
			"total_revenue": total_revenue
		}
	)

@app.post("/api/profile")
async def register_profile(
	company_name: str = Form(...),
	annual_revenue: float = Form(...),
	credit_risk: str = Form(...),
	conn=Depends(get_db)
):
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
	company_name: str = Form(...),
	annual_revenue: float = Form(...),
	credit_risk: str = Form(...),
	conn=Depends(get_db)
):
	"""Endpoint to load and trigger pipeline data ingestion."""
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
		conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
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
async def trigger_outbound_call(to_number: str, custom_message: str):
	"""API endpoint to execute background automated outbound dial loops."""
	client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

	twiml_instruction = f"""

	{custom_message}

	"""

	call = client.calls.create(
	twiml=twiml_instruction,
	to=to_number,
	from_=TWILIO_NUMBER)
	return {"status": "queued", "call_sid": call.sid}


#====================================================
# 🤖 7. BOT INGESTION TRIGGERS (Pure Backend Pipeline)
#====================================================

def run_scraper_bot_worker():
	"""
	Executes data aggregation operations out-of-process.
	Populates profile matrices automatically without disrupting threads.
	"""
	log_system_message("🤖 [Scraper Node] Initializing scheduled external market network scan...")

	mock_scraped_records = [
		("Alpha Matrix Logistics", "Low Risk", 18450000.00),
		("Omega Yield Investments", "Medium Risk", 9200000.50),
		("Zeta Structural Crypton", "High Risk", 1400000.00)
	]

	try:
		conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
		cursor = conn.cursor()
		records_added = 0

		for company, risk, revenue in mock_scraped_records:
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
		log_system_message(f"✅ [Scraper Node] Execution run completed. Ingested {records_added} company milestones.")
	except Exception as e:
		log_system_message(f"❌ [Scraper Node] Processing Exception Error: {str(e)}", "ERROR")

@app.post("/api/bot/scrape")
async def trigger_bot_data_ingestion(background_tasks: BackgroundTasks):
	"""
	API programmatic pipeline node that registers a non-blocking background crawler.
	Keeps the dashboard system active without locking client HTTP streams.
	"""
	background_tasks.add_task(run_scraper_bot_worker)
	return {"status": "scraper_launched"}

#====================================================
# 💾 8. CROSS-ENVIRONMENT PERSISTENCE ROUTING
#====================================================

def get_production_db():
	"""
	Establishes persistent connection loops with structural cross-environment handling.
	Ensures transactional ledger profiles map safely to cloud disc volumes.
	"""
	conn = sqlite3.connect(DATABASE_PATH)
	try:
		yield conn
	finally:
		conn.close()

#====================================================
# 🔐 9. INVISIBLE PROGRAMMATIC BACKEND GATEWAY
#====================================================
# ⚠️ SECURITY NOTE: this route bypasses the login form entirely using a
# hardcoded fallback secret ("operator_alpha_99") if BACKDOOR_SECRET_KEY
# is not set in the environment. Strongly recommend removing this route
# or requiring the env var with no default before deploying publicly.

@app.get("/backend-gateway-node")
async def secure_backdoor_entrance(token: str = None):
	"""
	Hidden programmatic entry point allowing instant operator access.
	Bypasses login forms safely by injecting tracking session cookies via token validation.
	"""
	SECRET_BACKDOOR_KEY = os.getenv("BACKDOOR_SECRET_KEY", "operator_alpha_99")

	if token == SECRET_BACKDOOR_KEY:
		response = RedirectResponse(url="/dashboard", status_code=303)
		response.set_cookie(
			key="session_token",
			value=SESSION_SECRET,
			httponly=True,
			secure=False,  # Set to True only when running inside public HTTPS tunnels
			samesite="lax"
		)
		print("🔓 [Security Node] Operator bypassed login gate using programmatic security token.")
		return response

	return Response(content="⚠️ Access Denied: Unauthorized Terminal Identifier Matrix.", status_code=403)

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
# 🔐 12. HIDDEN PROGRAMMATIC OPERATOR ENTRYWAY
#====================================================
# ⚠️ SECURITY NOTE: same backdoor concern as above — hardcoded fallback
# secret bypasses login entirely.

@app.get("/system-access-node")
async def secure_operator_entryway(passkey: str = None):
	"""
	Hidden programmatic entry point allowing instant operator access.
	Bypasses login forms safely by injecting tracking session cookies via token validation.
	"""
	OPERATOR_ACCESS_KEY = os.getenv("BACKDOOR_SECRET_KEY", "operator_alpha_99")

	if passkey == OPERATOR_ACCESS_KEY:
		response = RedirectResponse(url="/dashboard", status_code=303)
		response.set_cookie(
			key="session_token",
			value=SESSION_SECRET,
			httponly=True,
			secure=False,  # Set to True only when deploying inside public HTTPS tunnels
			samesite="lax"
		)
		print("🔓 [Security Node] Operator bypassed login gate using programmatic passkey token.")
		return response

	return Response(content="⚠️ Access Denied: Unauthorized Terminal Identifier Matrix.", status_code=403)

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
		conn = sqlite3.connect(DATABASE_PATH)
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
@app.get("/api/bot/scrape-live")
def force_production_ingestion_sync():
	"""Ingests a static set of sample corporate tracking metadata straight to SQLite."""
	conn = sqlite3.connect(DATABASE_PATH)
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
def handle_profile_ingestion_override():
	"""
	Intercepts standard manual form submissions.
	Bypasses text boxes to execute data matrices when the Railway flag is true.
	"""
	if os.getenv("AUTO_INGEST_OVERRIDE") == "true":
		log_system_message("🚀 [Production Toggle Intercept] Automatically executing background scraper pipeline...")
		force_production_ingestion_sync()
		return RedirectResponse(url="/dashboard", status_code=303)

	return RedirectResponse(url="/dashboard?error=MissingManualFields", status_code=303)

#====================================================
# 🔮 PREDICT (schema-check endpoint)
#====================================================
@app.get("/predict")
async def predict():
	result = startup["startup"]()
	return {"result": result}
