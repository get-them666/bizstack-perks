import csv
import io
import os
import secrets
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Form, Request, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.exceptions import HTTPException
from fastapi.templating import Jinja2Templates
from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

app = FastAPI(title="BizStack Perks Production Node")

# Mount templates engine for your cool dark mode layout
templates = Jinja2Templates(directory="templates")

# ====================================================
# PRODUCTION SECURITY CONFIGURATIONS
# ====================================================
MOCK_USERNAME = os.getenv("BIZSTACK_ADMIN_USER", "admin")
MOCK_PASSWORD = os.getenv("BIZSTACK_ADMIN_PASS", "password123")
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", secrets.token_hex(32))

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "ACxxxx")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "xxxxxx")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "+15550000000")

def get_db():
    # Production Adjustment: Save the file inside a persistent volume mount path if live
    # It falls back to a clean local file if you run it locally on your MacBook
    db_dir = "/app/data" if os.getenv("RAILWAY_ENVIRONMENT") else "."
    
    # Auto-create the directory folder path if missing on the cloud server disk
    if os.getenv("RAILWAY_ENVIRONMENT") and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        
    db_path = os.path.join(db_dir, "bizstack.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

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
    return templates.TemplateResponse("index.html", {"request": request})

# ====================================================
# 2. SECURITY GATE ROUTING (Authentication & Cookies)
# ====================================================
@app.get("/login", response_class=HTMLResponse)
async def login_gate(request: Request, error: str = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
async def process_login(username: str = Form(...), password: str = Form(...)):
    if username == MOCK_USERNAME and password == MOCK_PASSWORD:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="session_token", value=SESSION_SECRET, httponly=True, secure=True, samesite="lax")
        return response
    return RedirectResponse(url="/login?error=Invalid+Identifier+or+Keyphrase", status_code=333)

@app.get("/logout")
async def process_logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response

# ====================================================
# 3. SECURE PROFILE LEDGER (Protected Dashboard Window)
# ====================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_terminal(request: Request, conn=Depends(get_db)):
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

    cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles")
    profiles_data = cursor.fetchall()
    
    total_nodes = len(profiles_data)
    total_revenue = sum(float(profile[3]) for profile in profiles_data if profile and len(profile) > 3) if profiles_data else 0.0

    return templates.TemplateResponse(
        "dashboard.html", {
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
    cleaned_company = company_name.strip()
    cleaned_risk = credit_risk.strip()

    if len(cleaned_company) < 2 or len(cleaned_company) > 50 or annual_revenue < 0:
        raise HTTPException(status_code=400, detail="Validation Error: Invalid Ingestion Boundaries.")

    cursor = conn.cursor()
    try:
        # ✅ FIX: Match parameter order exactly with the targeted SQL columns
        cursor.execute(
            "INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
            (cleaned_company, cleaned_risk, annual_revenue)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
        
    return RedirectResponse(url="/dashboard", status_code=303)
@app.post("/api/profile")
async def register_profile(
    company_name: str = Form(...), 
    annual_revenue: float = Form(...), 
    credit_risk: str = Form(...), 
    conn=Depends(get_db)
):
    cleaned_company = company_name.strip()
    cleaned_risk = credit_risk.strip()

    if len(cleaned_company) < 2 or len(cleaned_company) > 50 or annual_revenue < 0:
        raise HTTPException(status_code=400, detail="Validation Error: Invalid Ingestion Boundaries.")

    cursor = conn.cursor()
    try:
        # ✅ FIX: Match parameter order exactly with the targeted SQL columns
        cursor.execute(
            "INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
            (cleaned_company, cleaned_risk, annual_revenue)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
        
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/api/profile/cleanup")
async def clear_profile_ledger(request: Request, conn=Depends(get_db)):
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM profiles")
    conn.commit()
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/profile/export")
async def export_profile_ledger(request: Request, conn=Depends(get_db)):
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)
        
    cursor = conn.cursor()
    cursor.execute("SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles")
    profiles_data = cursor.fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Node ID", "Commercial Entity", "Risk Matrix Rating", "Annual Milestone Revenue (USD)"])
    
    for row in profiles_data:
        writer.writerow([f"#00{row[0]}", row[1], row[2], f"{row[3]:.2f}"])
        
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]), 
        media_type="text/csv", 
        headers={"Content-Disposition": "attachment; filename=bizstack_ledger_backup.csv"}
    )

@app.get("/api/database/download")
async def download_raw_database(request: Request):
    session = request.cookies.get("session_token")
    if not session or session != SESSION_SECRET:
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)
        
    db_file_path = "bizstack.db"
    if not os.path.exists(db_file_path):
        raise HTTPException(status_code=404, detail="Database registry file not found.")
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(path=db_file_path, media_type="application/x-sqlite3", filename=f"bizstack_{timestamp}.db")

# ====================================================
# 4. TWILIO ROUTING AND WEBHOOKS
# ====================================================

@app.post("/twilio/inbound")
async def inbound_call():
    response = VoiceResponse()
    system_rules = read_agent_file("calling_rules.txt")
    gather = Gather(input="speech dtmf", action="/twilio/handle-response", num_digits=1, timeout=5)
    gather.say(f"Thank you for calling BizStack Perks. {system_rules}. Press 1 to reload.")
    response.append(gather)
    response.redirect("/twilio/inbound")
    return Response(content=str(response), media_type="application/xml")

@app.post("/twilio/handle-response")
async def handle_response(Digits: str = Form(None), SpeechResult: str = Form(None)):
    response = VoiceResponse()
    choice = Digits or (SpeechResult.lower() if SpeechResult else "")
    if "1" in choice:
        response.say("Processing entry verified. Goodbye.")
    else:
        response.redirect("/twilio/inbound")
    return Response(content=str(response), media_type="application/xml")

@app.post("/api/trigger-outbound")
async def trigger_outbound_call(to_number: str, custom_message: str):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls.create(
        twiml=f"<Response><Say voice='Polly.Joanna'>{custom_message}</Say></Response>",
        to=to_number,
        from_=TWILIO_NUMBER
    )
    return {"status": "queued", "call_sid": call.sid}
