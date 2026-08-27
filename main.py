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

# ====================================================
# GLOBAL CONFIGURATION CONSTANTS
# ====================================================
MOCK_USERNAME = os.getenv("BIZSTACK_ADMIN_USER", "admin")
MOCK_PASSWORD = os.getenv("BIZSTACK_ADMIN_PASS", "MatrixSecurePerks2026!")
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", "MatrixSecurePerks2026!")
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join("data", "bizstack.db"))

def log_system_message(message: str, level: str = "INFO"):
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_row = f"[{current_timestamp}] {level}: {message}\n"
    try:
        with open("api_server.log", "a") as f:
            f.write(log_row)
    except Exception:
        pass

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
    conn.commit()
    conn.close()
    log_system_message(f"📡 Schema validation passed for: {DATABASE_PATH}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_and_build_production_schema_startup()
    yield

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

# ====================================================
# PRODUCTION PATH INTERFACE ROUTING
# ====================================================
@app.get("/", response_class=HTMLResponse)
async def dynamic_root_gateway(request: Request):
    session = request.cookies.get("session_token")
    if session and session == SESSION_SECRET:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def serve_login_view(request: Request, error: str = None):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})

@app.post("/login")
async def forced_literal_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == MOCK_USERNAME and password == MOCK_PASSWORD:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="session_token", value=SESSION_SECRET, httponly=True, secure=True, samesite="lax")
        return response
    return RedirectResponse(url="/login?error=Invalid+Credentials", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_terminal(request: Request, conn=Depends(get_db)):
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)
    cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles")
    profiles_data = cursor.fetchall()
    total_nodes = len(profiles_data)
    total_revenue = sum(profile[3] for profile in profiles_data) if profiles_data else 0.0
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={
            "profiles": profiles_data, 
            "total_nodes": total_nodes, 
            "total_revenue": total_revenue
        }
    )


# ====================================================
# PATCH: ALL-INCLUSIVE HOME & LEGACY PATH MATCHING
# ====================================================
@app.get("/index")
@app.get("/index.html")
async def multi_path_home_fallback(request: Request):
    """Catch all variations of home keywords to route safely back to root domain."""
    return RedirectResponse(url="/", status_code=301)

if __name__ == '__main__':
    import uvicorn
    container_port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=container_port, reload=True)

# ====================================================
# PATCH: DIRECT ROUTING INTERFACE FOR STANDARD LOGOUT
# ====================================================
@app.get("/logout")
async def explicit_production_logout():
    """Clears system authorization context tokens and routes back to login."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response

# ====================================================
# PATCH: RESTORE COMPLETE BOT AUTOMATION PIPELINES
# ====================================================
FINNHUB_PROFILE_URL = "https://finnhub.io"

def configured_tickers():
    return [ticker.strip().upper() for ticker in os.getenv("FINNHUB_TICKERS", "AAPL,MSFT,GOOGL").split(",") if ticker.strip()]

def run_finnhub_sync():
    api_token = os.getenv("FINNHUB_DATA_KEY")
    tickers = configured_tickers()
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bot_runs (source, status, message) VALUES (?, ?, ?)", ("Finnhub Stock Profile 2", "running", f"Tickers: {', '.join(tickers)}"))
    run_id = cursor.lastrowid
    conn.commit()
    records_added = 0
    try:
        if not api_token: raise RuntimeError("FINNHUB_DATA_KEY is not configured")
        for ticker in tickers:
            response = requests.get(FINNHUB_PROFILE_URL, params={"symbol": ticker, "token": api_token}, timeout=10)
            response.raise_for_status()
            data = response.json()
            company_name = data.get("name")
            if not company_name: continue
            industry = data.get("finnhubIndustry") or "General Commercial"
            market_cap = float(data.get("marketCapitalization") or 0) * 1_000_000
            try:
                cursor.execute("INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)", (company_name, f"Finnhub: {industry}", market_cap))
                records_added += 1
            except sqlite3.IntegrityError: pass
        message = f"Synced {len(tickers)} ticker(s): {', '.join(tickers)}"
        cursor.execute("UPDATE bot_runs SET status = ?, completed_at = CURRENT_TIMESTAMP, records_added = ?, message = ? WHERE id = ?", ("success", records_added, message, run_id))
        conn.commit()
    except Exception as exc:
        cursor.execute("UPDATE bot_runs SET status = ?, completed_at = CURRENT_TIMESTAMP, records_added = ?, message = ? WHERE id = ?", ("failed", records_added, str(exc)[:500], run_id))
        conn.commit()
    finally: conn.close()

@app.post("/api/bot/run")
@app.get("/api/bot/run-fallback")  # <-- Fallback allowing browser tab click executions
async def run_bot_now(request: Request, background_tasks: BackgroundTasks):
    """Allows an operator to trigger real-data sync loops on demand."""
    background_tasks.add_task(run_finnhub_sync)
    return RedirectResponse(url="/dashboard?status=sync_launched", status_code=303)

@app.get("/api/database/download")
async def stream_raw_database_binary(request: Request):
    """Programmatic down-stream file export endpoint."""
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return Response(content="Unauthorized Access Block", status_code=401)
    if os.path.exists(DATABASE_PATH):
        return FileResponse(path=DATABASE_PATH, filename="bizstack_workspace_backup.db", media_type="application/x-sqlite3")
    return Response(content="Database storage binary not found.", status_code=404)

# ====================================================
# PATCH: EXPLICIT STATIC HOMEPAGE ROUTING FALLBACK
# ====================================================

# ====================================================
# PATCH: STRIPE SECURE PAYMENT CHECKOUT MATRIX
# ====================================================
@app.post("/api/stripe/create-checkout")
async def create_stripe_payment_session(request: Request):
    """Generates secure single-use checkout sessions mapped to your live domain."""
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe API keys are unassigned in environment parameters")
        
    try:
        checkout_session = stripe.checkout.session.create(
            payment_method_types=['card', 'us_bank_account'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': 'BizStack Perks Operator Access'},
                    'unit_amount': 2900,  # Value in cents ($29.00)
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url='https://bizstackperks.com',
            cancel_url='https://bizstackperks.com',
        )
        return {"checkout_url": checkout_session.url}
    except Exception as stripe_error:
        raise HTTPException(status_code=400, detail=str(stripe_error))
