import os
import re
import secrets
import sqlite3
import logging
from datetime import datetime
import json
from contextlib import asynccontextmanager
from typing import Optional, List

import stripe
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Gather, VoiceResponse
from fastapi.middleware.cors import CORSMiddleware

from lead_sources import GooglePlacesLeadSource, CensusLeadAnalyzer, AffiliateLeadNetwork, store_leads_to_db
from sms_manager import TwilioSMSManager, SMSNotification, handle_inbound_sms
from lead_analytics import LeadHotspotAnalyzer
from affiliate_manager import AffiliateCommissionManager, AffiliatePartner
from voice_bot import VoiceBotResponseGenerator, create_voice_greeting, create_callback_confirmation, create_information_response, create_menu_fallback, NATURAL_VOICE

try:
    from financial_super_agent import UnifiedFinancialDatabase, execute_super_agent_extraction, IntegratedMarketRecord
except ImportError:
    UnifiedFinancialDatabase = None
    IntegratedMarketRecord = None

logger = logging.getLogger(__name__)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "bizstack.db"))
MOCK_USERNAME = os.getenv("BIZSTACK_ADMIN_USER", "admin")
MOCK_PASSWORD = os.getenv("BIZSTACK_ADMIN_PASS", "password123")
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", secrets.token_hex(32))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PRICE_ID = os.getenv("PRICE_ID", "")
OFFER_PRICE_DISPLAY = os.getenv("OFFER_PRICE_DISPLAY", "$49 / month")
LEAD_CONSENT_TEXT = os.getenv(
    "LEAD_CONSENT_TEXT",
    "By submitting, you agree that BizStack Perks may contact you by call, text, and email "
    "about your request. Consent is not a condition of purchase. Message and data rates may apply.",
)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", os.getenv("TWILIO_NUMBER", ""))

# Lead source APIs
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
AFFILIATE_PARTNERS_CONFIG = os.getenv("AFFILIATE_PARTNERS_CONFIG", "[]")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def normalize_base_url(request: Optional[Request] = None) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8000"


def is_authenticated(request: Request) -> bool:
    return request.cookies.get("session_token") == SESSION_SECRET


def require_api_key_or_session(request: Request, x_api_key: Optional[str]) -> None:
    if is_authenticated(request):
        return
    if BOT_API_TOKEN and x_api_key and secrets.compare_digest(x_api_key, BOT_API_TOKEN):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def xml_response(voice_response: VoiceResponse) -> Response:
    return Response(content=str(voice_response), media_type="application/xml")


def upsert_payment(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    status: str,
    customer_id: Optional[str] = None,
    payment_intent_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
    amount_total: Optional[int] = None,
    currency: Optional[str] = None,
    customer_email: Optional[str] = None,
    raw_event_id: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO payments (
            stripe_session_id,
            stripe_customer_id,
            stripe_payment_intent_id,
            stripe_subscription_id,
            status,
            amount_total,
            currency,
            customer_email,
            raw_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_session_id) DO UPDATE SET
            stripe_customer_id = excluded.stripe_customer_id,
            stripe_payment_intent_id = excluded.stripe_payment_intent_id,
            stripe_subscription_id = excluded.stripe_subscription_id,
            status = excluded.status,
            amount_total = excluded.amount_total,
            currency = excluded.currency,
            customer_email = excluded.customer_email,
            raw_event_id = COALESCE(excluded.raw_event_id, payments.raw_event_id),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            session_id,
            customer_id,
            payment_intent_id,
            subscription_id,
            status,
            amount_total,
            currency,
            customer_email,
            raw_event_id,
        ),
    )
    conn.commit()


def record_checkout_session(conn: sqlite3.Connection, session_data: dict, event_id: Optional[str] = None) -> None:
    customer_details = session_data.get("customer_details") or {}
    payment_status = session_data.get("payment_status") or session_data.get("status") or "pending"
    if payment_status == "no_payment_required":
        payment_status = "paid"

    upsert_payment(
        conn,
        session_id=session_data["id"],
        customer_id=session_data.get("customer"),
        payment_intent_id=session_data.get("payment_intent"),
        subscription_id=session_data.get("subscription"),
        status=payment_status,
        amount_total=session_data.get("amount_total"),
        currency=session_data.get("currency"),
        customer_email=customer_details.get("email") or session_data.get("customer_email"),
        raw_event_id=event_id,
    )


def upsert_call_event(
    conn: sqlite3.Connection,
    *,
    call_sid: str,
    direction: Optional[str],
    call_status: Optional[str],
    from_number: Optional[str],
    to_number: Optional[str],
    message: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO call_events (call_sid, direction, call_status, from_number, to_number, message)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(call_sid) DO UPDATE SET
            direction = COALESCE(excluded.direction, call_events.direction),
            call_status = COALESCE(excluded.call_status, call_events.call_status),
            from_number = COALESCE(excluded.from_number, call_events.from_number),
            to_number = COALESCE(excluded.to_number, call_events.to_number),
            message = COALESCE(excluded.message, call_events.message),
            updated_at = CURRENT_TIMESTAMP
        """,
        (call_sid, direction, call_status, from_number, to_number, message),
    )
    conn.commit()


def stripe_ready() -> bool:
    return bool(STRIPE_SECRET_KEY and PRICE_ID)


def twilio_ready() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER)


def create_outbound_twiml(message: str) -> str:
    response = VoiceResponse()
    response.say(message, voice=NATURAL_VOICE)
    response.hangup()
    return str(response)


def affiliate_partners() -> list[dict[str, str]]:
    try:
        partners = json.loads(os.getenv("AFFILIATE_PARTNERS_JSON", "[]"))
    except json.JSONDecodeError:
        logger.warning("AFFILIATE_PARTNERS_JSON is not valid JSON")
        return []

    if not isinstance(partners, list):
        logger.warning("AFFILIATE_PARTNERS_JSON must be an array")
        return []

    return [
        {"name": partner["name"], "url": partner["url"], "description": partner.get("description", "")}
        for partner in partners
        if isinstance(partner, dict)
        and isinstance(partner.get("name"), str)
        and isinstance(partner.get("url"), str)
        and partner["url"].startswith(("https://", "http://"))
    ]


class OutboundCallRequest(BaseModel):
    to_number: str = Field(..., description="Destination number in E.164 format")
    message: str = Field(
        default="Hello from BizStack Perks. Your automated voice workflow is now live.",
        max_length=280,
    )

    @field_validator("to_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
            raise ValueError("to_number must be an E.164 phone number")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")
        return cleaned


class OutboundSmsRequest(BaseModel):
    lead_id: int
    message: str = Field(..., min_length=1, max_length=1_600)


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT UNIQUE NOT NULL,
                credit_risk_rating TEXT,
                annual_revenue REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stripe_session_id TEXT UNIQUE NOT NULL,
                stripe_customer_id TEXT,
                stripe_payment_intent_id TEXT,
                stripe_subscription_id TEXT,
                status TEXT NOT NULL,
                amount_total INTEGER,
                currency TEXT,
                customer_email TEXT,
                raw_event_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS call_events (
                call_sid TEXT PRIMARY KEY,
                direction TEXT,
                call_status TEXT,
                from_number TEXT,
                to_number TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                application_type TEXT NOT NULL,
                requested_product TEXT NOT NULL,
                requested_amount REAL,
                source TEXT NOT NULL,
                consent_text TEXT NOT NULL,
                consented_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS message_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER,
                message_sid TEXT UNIQUE,
                direction TEXT NOT NULL,
                channel TEXT NOT NULL,
                from_number TEXT,
                to_number TEXT,
                body TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    
    # Initialize affiliate manager to create necessary tables
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        AffiliateCommissionManager(conn)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="BizStack Perks",
    description="Lead generation and monetization platform for service businesses",
    version="2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize lead source managers
places_source = GooglePlacesLeadSource(GOOGLE_PLACES_API_KEY) if GOOGLE_PLACES_API_KEY else None
census_analyzer = CensusLeadAnalyzer(CENSUS_API_KEY) if CENSUS_API_KEY else None
sms_manager = TwilioSMSManager(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER)

# Affiliate partners from config
try:
    affiliate_config = json.loads(AFFILIATE_PARTNERS_CONFIG)
    if affiliate_config and isinstance(affiliate_config, list):
        affiliate_network = AffiliateLeadNetwork(affiliate_config)
    else:
        affiliate_network = None
except json.JSONDecodeError:
    affiliate_network = None

# Optional financial intelligence (if available)
financial_intelligence_db = None
if UnifiedFinancialDatabase is not None:
    financial_intelligence_db = UnifiedFinancialDatabase("production_market_intelligence.db")


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "BizStack Perks Lead Generation"}


@app.get("/api/config/features")
def get_feature_config():
    """Return enabled features based on environment config."""
    return {
        "stripe_checkout": stripe_ready(),
        "twilio_sms": sms_manager.is_configured(),
        "twilio_voice": twilio_ready(),
        "google_places": places_source is not None,
        "census_analytics": census_analyzer is not None,
        "affiliate_network": affiliate_network is not None,
        "financial_intelligence": financial_intelligence_db is not None,
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": error,
            "checkout_enabled": stripe_ready(),
            "offer_price_display": OFFER_PRICE_DISPLAY,
            "support_phone_href": f"tel:{TWILIO_PHONE_NUMBER}" if TWILIO_PHONE_NUMBER else "#contact",
            "support_phone_display": TWILIO_PHONE_NUMBER or "Schedule a strategy call",
            "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        },
    )


@app.get("/checkout/success", response_class=HTMLResponse)
async def checkout_success(request: Request, session_id: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="checkout_success.html",
        context={"session_id": session_id},
    )


@app.get("/checkout/cancel", response_class=HTMLResponse)
async def checkout_cancel(request: Request):
    return templates.TemplateResponse(request=request, name="checkout_cancel.html", context={})


@app.get("/apply", response_class=HTMLResponse)
async def application_form(request: Request, application_type: str = "business"):
    if application_type not in {"business", "consumer"}:
        raise HTTPException(status_code=404, detail="Application type not found")
    return templates.TemplateResponse(
        request=request,
        name="apply.html",
        context={"application_type": application_type, "consent_text": LEAD_CONSENT_TEXT},
    )


@app.post("/api/leads/submit")
async def submit_lead(
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    application_type: str = Form(...),
    requested_product: str = Form(...),
    requested_amount: Optional[float] = Form(default=None),
    source: str = Form(default="website"),
    consent: Optional[str] = Form(default=None),
    conn=Depends(get_db),
):
    if application_type not in {"business", "consumer"}:
        raise HTTPException(status_code=422, detail="Invalid application type")
    if not all((full_name.strip(), email.strip(), requested_product.strip())):
        raise HTTPException(status_code=422, detail="Complete the required fields")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone.strip()):
        raise HTTPException(status_code=422, detail="Enter a valid phone number")
    if consent != "accepted":
        raise HTTPException(status_code=422, detail="Consent is required")

    cursor = conn.execute(
        """
        INSERT INTO leads (
            full_name, email, phone, application_type, requested_product, requested_amount,
            source, consent_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            full_name.strip(),
            email.strip().lower(),
            phone.strip(),
            application_type,
            requested_product.strip(),
            requested_amount,
            source.strip()[:120] or "website",
            LEAD_CONSENT_TEXT,
        ),
    )
    conn.commit()
    return RedirectResponse(url=f"/application/received?lead_id={cursor.lastrowid}", status_code=303)


@app.get("/application/received", response_class=HTMLResponse)
async def application_received(request: Request, lead_id: Optional[int] = None):
    return templates.TemplateResponse(
        request=request,
        name="application_received.html",
        context={"lead_id": lead_id},
    )


@app.get("/affiliates", response_class=HTMLResponse)
async def affiliates(request: Request, conn=Depends(get_db)):
    # Get partners from config + database partners
    config_partners = affiliate_partners()
    
    # Optionally add database partners
    try:
        affiliate_mgr = AffiliateCommissionManager(conn)
        db_partners = affiliate_mgr.list_active_partners()
        # Combine and deduplicate by name
        all_partners = {p["name"]: p for p in db_partners}
        for p in config_partners:
            if p["name"] not in all_partners:
                all_partners[p["name"]] = p
        partners_list = list(all_partners.values())
    except Exception as e:
        logger.warning(f"Error loading database partners: {e}")
        partners_list = config_partners
    
    return templates.TemplateResponse(
        request=request,
        name="affiliates.html",
        context={"partners": partners_list},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})


@app.post("/login")
async def process_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == MOCK_USERNAME and password == MOCK_PASSWORD:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key="session_token",
            value=SESSION_SECRET,
            httponly=True,
            secure=normalize_base_url(request).startswith("https://"),
            samesite="lax",
        )
        return response
    return RedirectResponse(url="/login?error=Invalid+credentials", status_code=303)


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, conn=Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

    profiles = conn.execute(
        "SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles ORDER BY created_at DESC"
    ).fetchall()
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"profiles": profiles})


@app.get("/client", response_class=HTMLResponse)
async def client_registry(request: Request, conn=Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

    profiles = conn.execute(
        "SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles ORDER BY created_at DESC"
    ).fetchall()
    return templates.TemplateResponse(request=request, name="client.html", context={"profiles": profiles})


@app.get("/admin", response_class=HTMLResponse)
async def admin_workspace(request: Request, conn=Depends(get_db)):
    if not is_authenticated(request):
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "profiles": conn.execute(
                "SELECT company_name, credit_risk_rating, annual_revenue, created_at FROM profiles ORDER BY created_at DESC LIMIT 100"
            ).fetchall(),
            "leads": conn.execute(
                """
                SELECT full_name, email, phone, application_type, requested_product, requested_amount,
                       source, consented_at, status
                FROM leads ORDER BY created_at DESC LIMIT 100
                """
            ).fetchall(),
            "payments": conn.execute(
                """
                SELECT stripe_session_id, status, amount_total, currency, customer_email, updated_at
                FROM payments ORDER BY updated_at DESC LIMIT 100
                """
            ).fetchall(),
            "calls": conn.execute(
                """
                SELECT call_sid, direction, call_status, from_number, to_number, message, updated_at
                FROM call_events ORDER BY updated_at DESC LIMIT 100
                """
            ).fetchall(),
        },
    )


@app.post("/api/pipeline-load-trigger")
async def add_profile(
    company_name: str = Form(...),
    annual_revenue: float = Form(...),
    credit_risk: str = Form(...),
    conn=Depends(get_db),
):
    try:
        conn.execute(
            "INSERT INTO profiles (company_name, credit_risk_rating, annual_revenue) VALUES (?, ?, ?)",
            (company_name.strip(), credit_risk, annual_revenue),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return RedirectResponse(url="/dashboard?error=Company+already+exists", status_code=303)
    except sqlite3.DatabaseError:
        return RedirectResponse(url="/dashboard?error=Database+error", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/api/profiles")
async def get_profiles(request: Request, conn=Depends(get_db)):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    profiles = conn.execute(
        "SELECT id, company_name, credit_risk_rating, annual_revenue FROM profiles ORDER BY created_at DESC"
    ).fetchall()
    return {
        "profiles": [
            {
                "id": profile["id"],
                "company_name": profile["company_name"],
                "risk_rating": profile["credit_risk_rating"],
                "revenue": profile["annual_revenue"],
            }
            for profile in profiles
        ]
    }


@app.post("/api/profile/delete/{profile_id}")
async def delete_profile(profile_id: int, request: Request, conn=Depends(get_db)):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()
    return {"status": "deleted"}


@app.post("/api/checkout/create")
async def create_checkout_session(
    request: Request,
    email: Optional[str] = Form(default=None),
    business_name: Optional[str] = Form(default=None),
    conn=Depends(get_db),
):
    if not stripe_ready():
        return RedirectResponse(url="/?error=Checkout+is+not+configured+yet", status_code=303)

    stripe_client = stripe.StripeClient(STRIPE_SECRET_KEY)
    base_url = normalize_base_url(request)
    metadata = {}
    if business_name and business_name.strip():
        metadata["business_name"] = business_name.strip()[:120]

    checkout_params = {
        "mode": "payment",
        "line_items": [{"price": PRICE_ID, "quantity": 1}],
        "customer_email": (email or "").strip() or None,
        "success_url": f"{base_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/checkout/cancel",
        "metadata": metadata,
    }

    try:
        session = stripe_client.checkout.sessions.create(params=checkout_params)
    except stripe.InvalidRequestError as exc:
        if not (exc.param or "").startswith("line_items[0]"):
            logger.warning("Stripe Checkout configuration error: code=%s param=%s", exc.code, exc.param)
            return RedirectResponse(url="/?error=Unable+to+start+checkout", status_code=303)

        logger.warning("Configured Stripe Price ID cannot be used; using the configured $49 checkout item")
        checkout_params["line_items"] = [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"BizStack Perks Entry Plan - {business_name or 'Client Portal'}",
                    },
                    "unit_amount": 4900,
                },
                "quantity": 1,
            }
        ]
        try:
            session = stripe_client.checkout.sessions.create(params=checkout_params)
        except stripe.error.StripeError as fallback_error:
            logger.warning(
                "Stripe Checkout fallback failed: code=%s param=%s",
                fallback_error.code,
                fallback_error.param,
            )
            return RedirectResponse(url="/?error=Unable+to+start+checkout", status_code=303)
    except stripe.error.StripeError as exc:
        logger.warning("Stripe Checkout failed: code=%s param=%s", exc.code, exc.param)
        return RedirectResponse(url="/?error=Unable+to+start+checkout", status_code=303)

    session_data = session.to_dict() if hasattr(session, "to_dict") else session
    record_checkout_session(
        conn,
        {
            "id": session_data["id"],
            "customer": session_data.get("customer"),
            "payment_intent": session_data.get("payment_intent"),
            "subscription": session_data.get("subscription"),
            "payment_status": session_data.get("payment_status", "unpaid"),
            "status": session_data.get("status", "open"),
            "amount_total": session_data.get("amount_total"),
            "currency": session_data.get("currency"),
            "customer_email": (email or "").strip() or None,
            "customer_details": {"email": (email or "").strip() or None},
        },
    )
    return RedirectResponse(url=session_data["url"], status_code=303)


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, conn=Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not STRIPE_WEBHOOK_SECRET or not signature:
        raise HTTPException(status_code=400, detail="Missing webhook signature")

    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    if event.get("type") in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
    ):
        record_checkout_session(conn, event["data"]["object"], event.get("id"))

    return JSONResponse({"received": True})


@app.post("/twilio/voice/incoming")
@app.post("/twilio/inbound")
async def inbound_call():
    """Handle inbound calls with AI-powered conversation bot."""
    return Response(content=create_voice_greeting(), media_type="application/xml")


@app.post("/twilio/voice/process-input")
async def process_voice_input(
    SpeechResult: Optional[str] = Form(default=None),
    Digits: Optional[str] = Form(default=None),
    CallSid: Optional[str] = Form(default=None),
    request: Request = None,
    conn=Depends(get_db),
):
    """Process speech or DTMF input from caller and generate contextual response."""
    user_input = (SpeechResult or Digits or "").strip()

    if not user_input:
        return xml_response(create_menu_fallback())

    # Initialize bot
    bot = VoiceBotResponseGenerator(OPENAI_API_KEY)
    
    try:
        # Generate AI response (call_sid gives the bot memory of this call)
        response_text = await bot.generate_response(user_input, call_sid=CallSid)
    except Exception as e:
        logger.error(f"Voice bot error: {e}")
        response_text = bot._fallback_response(user_input, None)

    response = VoiceResponse()
    response.say(response_text, voice=NATURAL_VOICE, language="en-US")

    # Prompt for callback if needed
    if any(word in user_input.lower() for word in ["yes", "callback", "call back", "speak"]):
        response.say(
            "Please stay on the line for one quick question.",
            voice=NATURAL_VOICE,
        )
        gather = Gather(
            input="dtmf",
            action="/twilio/voice/capture-callback",
            method="POST",
            timeout=5,
            num_digits=1,
        )
        gather.say("Press 1 to confirm your callback, or 2 to end the call.", voice=NATURAL_VOICE)
        response.append(gather)
    else:
        # Ask follow-up
        gather = Gather(
            input="speech",
            action="/twilio/voice/process-input",
            method="POST",
            timeout=5,
            speech_timeout="auto",
        )
        gather.say("Do you have any other questions?", voice=NATURAL_VOICE)
        response.append(gather)

    return xml_response(response)


@app.post("/twilio/voice/capture-callback")
async def capture_callback(
    Digits: Optional[str] = Form(default=None),
    From: Optional[str] = Form(default=None),
    CallSid: Optional[str] = Form(default=None),
    conn=Depends(get_db),
):
    """Confirm and record callback request."""
    response = VoiceResponse()

    if Digits == "1":
        # Confirmed callback
        phone = From or "unknown"
        upsert_call_event(
            conn,
            call_sid=CallSid or "unknown",
            direction="inbound-callback",
            call_status="callback_requested",
            from_number=phone,
            to_number=TWILIO_PHONE_NUMBER,
            message="Callback requested from inbound call",
        )
        response.say(
            "Thank you! A specialist from BizStack Perks will reach out to you within 24 hours. Goodbye!",
            voice=NATURAL_VOICE,
        )
    else:
        response.say("Thank you for calling BizStack Perks. Goodbye!", voice=NATURAL_VOICE)

    response.hangup()
    return xml_response(response)


@app.post("/twilio/voice/handle-dtmf")
async def handle_dtmf(Digits: Optional[str] = Form(default=None)):
    """Handle legacy DTMF menu navigation."""
    response = VoiceResponse()

    if Digits == "1":
        response.say(
            f"Our entry plan starts at {OFFER_PRICE_DISPLAY}. "
            f"You get lead discovery, SMS follow-ups, and analytics. "
            f"Ready to get started?",
            voice=NATURAL_VOICE,
        )
    elif Digits == "2":
        response.say(
            "We automatically discover and send you qualified leads, handle SMS outreach, "
            "and give you real-time conversion analytics. "
            "Everything you need to scale.",
            voice=NATURAL_VOICE,
        )
    elif Digits == "3":
        response.say(
            "We'll set up a callback for you. A specialist will reach out within 24 hours.",
            voice=NATURAL_VOICE,
        )
    elif Digits == "4":
        response.say(
            "Visit bizstack-perks.com to learn more, sign up for a free trial, or schedule a demo. Goodbye!",
            voice=NATURAL_VOICE,
        )
    else:
        response.say("Invalid selection. Goodbye.", voice=NATURAL_VOICE)

    response.hangup()
    return xml_response(response)


@app.post("/twilio/voice/status")
@app.post("/twilio/status")
async def twilio_status(request: Request, conn=Depends(get_db)):
    form = await request.form()
    if TWILIO_AUTH_TOKEN:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        if not validator.validate(
            str(request.url),
            dict(form),
            request.headers.get("X-Twilio-Signature", ""),
        ):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    call_sid = form.get("CallSid")
    if call_sid:
        upsert_call_event(
            conn,
            call_sid=call_sid,
            direction=form.get("Direction"),
            call_status=form.get("CallStatus"),
            from_number=form.get("From"),
            to_number=form.get("To"),
        )
    return {"received": True}


@app.post("/api/twilio/voice/outbound")
async def trigger_outbound_call(
    payload: OutboundCallRequest,
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    require_api_key_or_session(request, x_api_key)
    if not twilio_ready():
        raise HTTPException(status_code=503, detail="Twilio voice is not configured")

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    try:
        call = client.calls.create(
            to=payload.to_number,
            from_=TWILIO_PHONE_NUMBER,
            twiml=create_outbound_twiml(payload.message),
            status_callback=f"{normalize_base_url(request)}/twilio/voice/status",
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to place outbound call") from exc

    upsert_call_event(
        conn,
        call_sid=call.sid,
        direction="outbound-api",
        call_status=getattr(call, "status", "queued"),
        from_number=TWILIO_PHONE_NUMBER,
        to_number=payload.to_number,
        message=payload.message,
    )
    return {"call_sid": call.sid, "status": getattr(call, "status", "queued")}





# ============================================================================
# NEW: SMS Messaging Endpoints
# ============================================================================


@app.post("/twilio/sms/inbound")
async def inbound_sms(request: Request, conn=Depends(get_db)):
    """Handle inbound SMS from leads."""
    if TWILIO_AUTH_TOKEN:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        form = await request.form()
        if not validator.validate(
            str(request.url),
            dict(form),
            request.headers.get("X-Twilio-Signature", ""),
        ):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    else:
        form = await request.form()

    response = handle_inbound_sms(
        message_body=form.get("Body", ""),
        from_phone=form.get("From", ""),
        message_sid=form.get("MessageSid", ""),
        conn=conn,
    )
    return Response(content=str(response), media_type="application/xml")


@app.post("/api/sms/send")
async def send_sms_to_lead(
    lead_id: int = Form(...),
    message: str = Form(...),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Send SMS to a lead (authenticated endpoint)."""
    require_api_key_or_session(request, x_api_key)
    if not sms_manager.is_configured():
        raise HTTPException(status_code=503, detail="SMS not configured")

    # Fetch lead
    lead = conn.execute(
        "SELECT id, phone, full_name FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    notif = SMSNotification(
        lead_id=lead_id,
        phone=lead["phone"],
        message=message,
    )

    result = sms_manager.send_sms(notif, conn)
    return result


# ============================================================================
# NEW: Lead Discovery & Analytics Endpoints
# ============================================================================


@app.post("/api/leads/discover")
async def discover_leads_from_location(
    location: str = Form(...),
    category: str = Form(...),
    radius_miles: int = Form(default=5),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    background_tasks: BackgroundTasks = None,
    conn=Depends(get_db),
):
    """Discover leads from Google Places by location + category."""
    require_api_key_or_session(request, x_api_key)
    if not places_source:
        raise HTTPException(status_code=503, detail="Google Places not configured")

    radius_meters = radius_miles * 1609.34

    try:
        leads = await places_source.search_by_location_and_category(
            location=location,
            category=category,
            radius=int(radius_meters),
        )
    except Exception as e:
        logger.error(f"Lead discovery error: {e}")
        raise HTTPException(status_code=500, detail="Lead discovery failed") from e

    count = store_leads_to_db(conn, leads)

    return {
        "location": location,
        "category": category,
        "leads_found": len(leads),
        "leads_stored": count,
        "leads": [l.dict() for l in leads[:10]],  # Return first 10
    }


@app.get("/api/analytics/hotspots")
async def get_lead_hotspots(
    days: int = 30,
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Get geographic hotspots with high lead density and conversion rates."""
    require_api_key_or_session(request, x_api_key)

    analyzer = LeadHotspotAnalyzer(conn)
    hotspots = analyzer.get_location_hotspots(days_lookback=days)
    recommendations = analyzer.recommend_ad_targets()

    return {
        "hotspots": hotspots,
        "recommendations": recommendations,
        "report_date": str(datetime.now().isoformat()),
    }


@app.get("/api/analytics/demand")
async def get_product_demand(
    days: int = 30,
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Get product/service demand by location."""
    require_api_key_or_session(request, x_api_key)

    analyzer = LeadHotspotAnalyzer(conn)
    demand = analyzer.get_product_demand_by_location(days_lookback=days)
    quality = analyzer.get_lead_quality_by_source()
    trends = analyzer.get_traffic_trends(days_lookback=days)

    return {
        "demand_by_location": demand,
        "lead_quality_by_source": quality,
        "traffic_trends": trends,
    }


@app.get("/api/analytics/trends")
async def get_traffic_trends(
    days: int = 30,
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Get daily traffic and conversion trends."""
    require_api_key_or_session(request, x_api_key)

    analyzer = LeadHotspotAnalyzer(conn)
    trends = analyzer.get_traffic_trends(days_lookback=days)

    return {"trends": trends}


# ============================================================================
# NEW: Affiliate Management Endpoints
# ============================================================================


@app.post("/api/affiliates/partner")
async def add_affiliate_partner(
    name: str = Form(...),
    contact_email: str = Form(...),
    commission_percentage: float = Form(...),
    payout_method: str = Form(...),
    payout_account: str = Form(...),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Add a new affiliate partner."""
    require_api_key_or_session(request, x_api_key)

    partner = AffiliatePartner(
        name=name,
        contact_email=contact_email,
        commission_percentage=commission_percentage,
        payout_method=payout_method,
        payout_account=payout_account,
    )

    affiliate_mgr = AffiliateCommissionManager(conn)
    partner_id = affiliate_mgr.add_partner(partner)

    return {"status": "created", "partner_id": partner_id, "partner": partner.dict()}


@app.get("/api/affiliates/earnings/{partner_id}")
async def get_affiliate_earnings(
    partner_id: int,
    days: Optional[int] = None,
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Get earnings for an affiliate partner."""
    require_api_key_or_session(request, x_api_key)

    affiliate_mgr = AffiliateCommissionManager(conn)
    earnings = affiliate_mgr.get_partner_earnings(partner_id, days_lookback=days)
    partner = affiliate_mgr.get_partner(partner_id)

    return {"partner": partner, "earnings": earnings}


@app.get("/api/affiliates/payouts")
async def get_pending_payouts(
    min_amount: float = 50.0,
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """Get pending payouts ready for processing."""
    require_api_key_or_session(request, x_api_key)

    affiliate_mgr = AffiliateCommissionManager(conn)
    batch = affiliate_mgr.generate_payout_batch(min_amount=min_amount)

    return batch


from datetime import datetime