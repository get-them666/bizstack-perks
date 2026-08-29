from fastapi import FastAPI, Form, responses, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import stripe
import os
import sqlite3

app = FastAPI()

# Mount templates and static directory so your checkout HTML pages don't 404
try:
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")
except Exception:
    pass

templates = Jinja2Templates(directory="templates")

# Read environmental config parameters from Railway setup
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
DOMAIN = os.getenv("DOMAIN", "https://bizstackperks.com").rstrip('/')

# --- 1. RESTORED: Front-End Dashboard & Login Views ---
@app.get("/")
async def render_root_view(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception:
        return responses.PlainTextResponse("BizStack Perks Home Interface Ready.")

@app.get("/login")
async def render_login_view(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception:
        return responses.PlainTextResponse("Login view terminal interface ready.")

@app.get("/dashboard")
async def render_dashboard_view(request: Request):
    leads_data = []
    try:
        conn = sqlite3.connect('bizstack.db')
        cursor = conn.cursor()
        cursor.execute("SELECT name, credit_tier, requested_capital, status FROM leads ORDER BY id DESC LIMIT 20;")
        leads_data = cursor.fetchall()
        conn.close()
    except Exception:
        pass

    try:
        return templates.TemplateResponse("dashboard.html", {"request": request, "leads": leads_data})
    except Exception:
        try:
            return templates.TemplateResponse("index.html", {"request": request})
        except Exception:
            return responses.PlainTextResponse("Dashboard loaded (no template found).")

# --- 2. PRESERVED: Fixed Stripe Payment Gateway Routing ---
@app.post("/api/checkout/create")
async def create_checkout_session(email: str = Form(None), business_name: str = Form(None)):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            customer_email=email if email else None,
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"BizStack Perks Entry Plan - {business_name or 'Client Portal'}",
                    },
                    'unit_amount': 4900, # $49.00 USD
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{DOMAIN}/dashboard?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/?error=checkout_cancelled",
        )
        return responses.RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        print(f"[STRIPE RUNTIME ERROR]: {str(e)}")
        return responses.RedirectResponse(url=f"{DOMAIN}/?error=Unable+to+start+checkout", status_code=303)
