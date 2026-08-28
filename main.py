import os
import secrets
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Form, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather

app = FastAPI(title="BizStack Perks")

# Templates
templates = Jinja2Templates(directory="templates")

# Database setup
DATABASE_PATH = "bizstack.db"
MOCK_USERNAME = os.getenv("BIZSTACK_ADMIN_USER", "admin")
MOCK_PASSWORD = os.getenv("BIZSTACK_ADMIN_PASS", "password123")
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", secrets.token_hex(32))

# Twilio setup
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER", "")

def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

@app.on_event("startup")
def init_db():
    """Initialize database tables on startup"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT UNIQUE NOT NULL,
            credit_risk_rating TEXT,
            annual_revenue REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# ==================== AUTHENTICATION ====================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    """Process login"""
    if username == MOCK_USERNAME and password == MOCK_PASSWORD:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key="session_token",
            value=SESSION_SECRET,
            httponly=True,
            secure=True,
            samesite="lax"
        )
        return response
    return RedirectResponse(url="/login?error=Invalid+credentials", status_code=303)

@app.get("/logout")
async def logout():
    """Logout"""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response

# ==================== DASHBOARD ====================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, conn=Depends(get_db)):
    """Dashboard page"""
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)
    
    cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles ORDER BY created_at DESC")
    profiles = cursor.fetchall()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "profiles": profiles
    })

# ==================== PROFILE API ====================

@app.post("/api/pipeline-load-trigger")
async def add_profile(
    company_name: str = Form(...),
    annual_revenue: float = Form(...),
    credit_risk: str = Form(...),
    conn=Depends(get_db)
):
    """Add a new company profile"""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
            (company_name, credit_risk, annual_revenue)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/dashboard?error=Company+already+exists", status_code=303)
    except Exception as e:
        return RedirectResponse(url="/dashboard?error=Database+error", status_code=303)
    
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/profiles")
async def get_profiles(request: Request, conn=Depends(get_db)):
    """Get all profiles as JSON"""
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles ORDER BY created_at DESC")
    profiles = cursor.fetchall()
    
    return {
        "profiles": [
            {
                "id": p[0],
                "company_name": p[1],
                "risk_rating": p[2],
                "revenue": p[3]
            } for p in profiles
        ]
    }

@app.post("/api/profile/delete/{profile_id}")
async def delete_profile(profile_id: int, request: Request, conn=Depends(get_db)):
    """Delete a profile"""
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    cursor = conn.cursor()
    cursor.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()
    
    return {"status": "deleted"}

# ==================== TWILIO VOICE ====================

@app.post("/twilio/inbound")
async def inbound_call():
    """Handle inbound calls from Twilio"""
    response = VoiceResponse()
    
    gather = Gather(
        input="speech dtmf",
        action="/twilio/handle-input",
        num_digits=1,
        timeout=5,
        speech_timeout="auto"
    )
    gather.say("Welcome to BizStack Perks. Press 1 to hear company information, or press 2 to speak with an agent.")
    response.append(gather)
    
    response.redirect("/twilio/inbound")
    return response.to_xml()

@app.post("/twilio/handle-input")
async def handle_input(Digits: str = Form(None), SpeechResult: str = Form(None), conn=Depends(get_db)):
    """Handle user input from Twilio"""
    response = VoiceResponse()
    choice = Digits or (SpeechResult.lower() if SpeechResult else "").strip()
    
    if "1" in choice:
        # Read company info
        cursor = conn.cursor()
        cursor.execute("SELECT company_name, annual_revenue FROM profiles LIMIT 1")
        company = cursor.fetchone()
        
        if company:
            response.say(f"Our top company is {company[0]} with annual revenue of {company[1]} dollars.")
        else:
            response.say("No company data available at this time.")
        
        response.redirect("/twilio/inbound")
    
    elif "2" in choice:
        response.say("Transferring you to an agent. Please hold.")
        # In production, transfer to a real number or queue
        response.dial("+1234567890")  # Replace with your agent number
    
    else:
        response.say("I didn't catch that. Please try again.")
        response.redirect("/twilio/inbound")
    
    return response.to_xml()

@app.post("/twilio/status")
async def twilio_status(request: Request):
    """Webhook for Twilio call status updates"""
    return {"status": "ok"}

