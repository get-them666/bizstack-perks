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
async def serve_login_view(request: Request):
    return templates.TemplateResponse(request, "login.html")

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
    return templates.TemplateResponse(request, "dashboard.html", {"profiles": profiles_data, "total_nodes": total_nodes, "total_revenue": total_revenue})

if __name__ == "__main__":
    import uvicorn
    container_port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=container_port, reload=True)

@app.post("/api/bot/run")
async def receive_bot_handshake(request: Request):
    try:
        payload = await request.json()
        print(f"Received bot agent sync signal: {payload}")
        return {"status": "ACKNOWLEDGED", "received_payload": payload}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}
