import os
import secrets
import sqlite3
from fastapi import FastAPI, Form, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from twilio.twiml.voice_response import VoiceResponse, Gather

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DATABASE_PATH = "bizstack.db"
MOCK_USERNAME = "admin"
MOCK_PASSWORD = "password123"
SESSION_SECRET = secrets.token_hex(32)

def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    yield conn
    conn.close()

@app.on_event("startup")
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT UNIQUE NOT NULL,
            credit_risk_rating TEXT,
            annual_revenue REAL
        )
    """)
    conn.commit()
    conn.close()

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
async def login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    if username == MOCK_USERNAME and password == MOCK_PASSWORD:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie("session_token", SESSION_SECRET, httponly=True, secure=True, samesite="lax")
        return response
    return RedirectResponse(url="/login?error=Invalid+credentials", status_code=303)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response

@app.get("/dashboard")
async def dashboard(request: Request, conn=Depends(get_db)):
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Auth+Required", status_code=303)
    cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles")
    profiles = cursor.fetchall()
    return templates.TemplateResponse("dashboard.html", {"request": request, "profiles": profiles})

@app.post("/api/pipeline-load-trigger")
async def add_profile(company_name: str = Form(...), annual_revenue: float = Form(...), credit_risk: str = Form(...), conn=Depends(get_db)):
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)", (company_name, credit_risk, annual_revenue))
        conn.commit()
    except:
        pass
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/twilio/inbound")
async def inbound_call():
    response = VoiceResponse()
    gather = Gather(input="speech dtmf", action="/twilio/handle-input", num_digits=1, timeout=5)
    gather.say("Welcome to BizStack. Press 1 for info or 2 for agent.")
    response.append(gather)
    response.redirect("/twilio/inbound")
    return response.to_xml()

@app.post("/twilio/handle-input")
async def handle_input(Digits: str = Form(None), SpeechResult: str = Form(None), conn=Depends(get_db)):
    response = VoiceResponse()
    choice = Digits or (SpeechResult.lower() if SpeechResult else "")
    if "1" in choice:
        cursor = conn.cursor()
        cursor.execute("SELECT company_name FROM profiles LIMIT 1")
        company = cursor.fetchone()
        response.say(f"Top company: {company[0]}" if company else "No data available")
        response.redirect("/twilio/inbound")
    elif "2" in choice:
        response.say("Transferring to agent")
        response.dial("+1234567890")
    else:
        response.say("Try again")
        response.redirect("/twilio/inbound")
    return response.to_xml()

