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
from writeup_generator import generate_targeting_writeup
from business_signals import NewsSignalScanner, OpenDataPermitScanner, deduplicate_signals
from local_bank_rates import load_bank_rates, get_best_rates_for_region, format_rates_for_display, check_rate_staleness
from outreach_generator import generate_outreach_email, generate_bulk_outreach
from affiliate_manager import AffiliateCommissionManager, AffiliatePartner
from voice_bot import (
    VoiceBotResponseGenerator,
    create_voice_greeting,
    create_callback_confirmation,
    create_information_response,
    create_menu_fallback,
    create_outbound_sales_greeting,
    NATURAL_VOICE,
    detect_closing_intent,
    extract_business_name,
    get_call_state,
)
from customer_portal import (
    init_customer_tables,
    provision_customer_from_checkout,
    mark_subscription_status,
    get_customer_by_id,
    get_customer_by_phone,
    get_customer_by_email,
    get_customer_leads,
    generate_otp,
    verify_otp,
    create_portal_session_token,
    get_customer_by_session_token,
    clear_portal_session,
    link_phone_to_customer,
)
from email_notifier import send_portal_login_code, email_configured

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

# Telephony: supports Twilio OR SignalWire. If SignalWire credentials are
# present, they take priority (SignalWire's Project ID and API Token map to
# the same account_sid/auth_token slots Twilio uses, since SignalWire's REST
# API is Twilio-compatible). Falls back to TWILIO_* vars if SignalWire isn't
# configured, so existing Twilio setups keep working unchanged.
SIGNALWIRE_PROJECT_ID = os.getenv("SIGNALWIRE_PROJECT_ID", "")
SIGNALWIRE_API_TOKEN = os.getenv("SIGNALWIRE_API_TOKEN", "")
SIGNALWIRE_SPACE_URL = os.getenv("SIGNALWIRE_SPACE_URL", "")
SIGNALWIRE_PHONE_NUMBER = os.getenv("SIGNALWIRE_PHONE_NUMBER", "")

if SIGNALWIRE_PROJECT_ID and SIGNALWIRE_API_TOKEN and SIGNALWIRE_SPACE_URL:
    TWILIO_ACCOUNT_SID = SIGNALWIRE_PROJECT_ID
    TWILIO_AUTH_TOKEN = SIGNALWIRE_API_TOKEN
    TWILIO_PHONE_NUMBER = SIGNALWIRE_PHONE_NUMBER or os.getenv("TWILIO_PHONE_NUMBER", os.getenv("TWILIO_NUMBER", ""))
else:
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", os.getenv("TWILIO_NUMBER", ""))

# Lead source APIs
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
SENDER_COMPANY_NAME = os.getenv("SENDER_COMPANY_NAME", "BizStack Perks")
SENDER_PHYSICAL_ADDRESS = os.getenv("SENDER_PHYSICAL_ADDRESS", "")
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

    # Auto-provision a customer portal account once payment is actually confirmed.
    # This only runs on paid/complete statuses so unpaid or canceled sessions never
    # create an account.
    if payment_status in ("paid", "complete"):
        email = customer_details.get("email") or session_data.get("customer_email")
        metadata = session_data.get("metadata") or {}
        business_name = metadata.get("business_name") if isinstance(metadata, dict) else None
        provision_customer_from_checkout(
            conn,
            email=email,
            business_name=business_name,
            stripe_customer_id=session_data.get("customer"),
            stripe_subscription_id=session_data.get("subscription"),
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


def create_telephony_client() -> Client:
    """Create a Twilio-compatible REST client, pointed at SignalWire if configured."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    if SIGNALWIRE_SPACE_URL:
        space_url = SIGNALWIRE_SPACE_URL.strip().rstrip("/")
        if not space_url.startswith("http"):
            space_url = f"https://{space_url}"
        client.api.base_url = space_url
    return client


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


def load_perks_json_partners() -> list[dict[str, str]]:
    """Load the curated affiliate list from perks.json (repo root).

    This is the file verify_links.py checks, and is the source of truth for
    the /affiliates page. Update perks.json to add, remove, or change
    affiliate links -- no redeploy or environment variable edit needed.
    """
    perks_path = os.path.join(BASE_DIR, "perks.json")  # NOT under data/ -- that path is volume-mounted on Railway and would shadow this file
    try:
        with open(perks_path, encoding="utf-8") as f:
            partners = json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        logger.warning("perks.json contains invalid JSON")
        return []

    if not isinstance(partners, list):
        logger.warning("perks.json must be a JSON array")
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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS outreach_unsubscribes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_identifier TEXT UNIQUE NOT NULL,
                unsubscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        init_customer_tables(conn)
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
sms_manager = TwilioSMSManager(
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, signalwire_space_url=SIGNALWIRE_SPACE_URL or None
)

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
        "fred_banking_data": bool(FRED_API_KEY),
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
    # Primary source: perks.json (curated list, checked by verify_links.py).
    # Update that file to change what's shown here -- no redeploy needed.
    perks_json_partners = load_perks_json_partners()

    # Legacy/optional source: AFFILIATE_PARTNERS_JSON environment variable.
    config_partners = affiliate_partners()

    # Merge, preferring perks.json entries when names collide.
    all_partners = {p["name"]: p for p in config_partners}
    for p in perks_json_partners:
        all_partners[p["name"]] = p

    # Optionally add database-tracked commission partners too.
    try:
        affiliate_mgr = AffiliateCommissionManager(conn)
        db_partners = affiliate_mgr.list_active_partners()
        for p in db_partners:
            if p["name"] not in all_partners:
                all_partners[p["name"]] = p
    except Exception as e:
        logger.warning(f"Error loading database partners: {e}")

    partners_list = list(all_partners.values())

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
            "customers": conn.execute(
                """
                SELECT id, business_name, email, phone, subscription_status, created_at
                FROM customers ORDER BY created_at DESC LIMIT 100
                """
            ).fetchall(),
        },
    )


@app.post("/api/customers/create-manual")
async def create_customer_manual(
    email: Optional[str] = Form(default=None),
    phone: Optional[str] = Form(default=None),
    business_name: Optional[str] = Form(default=None),
    request: Request = None,
    conn=Depends(get_db),
):
    """
    Manually create a customer portal account, bypassing the checkout flow.
    Login-protected admin-only tool -- useful for testing the portal login
    flow or fixing an account that should have been auto-created but wasn't
    (e.g. a completed payment that didn't fire the webhook correctly).
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not email and not phone:
        raise HTTPException(status_code=422, detail="Provide at least an email or phone number")

    customer_id = provision_customer_from_checkout(
        conn,
        email=(email or "").strip().lower() or None,
        business_name=business_name,
        stripe_customer_id=None,
        phone=(phone or "").strip() or None,
    )

    if phone and customer_id:
        link_phone_to_customer(conn, customer_id, phone.strip())

    if not customer_id:
        raise HTTPException(status_code=500, detail="Failed to create customer record")

    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/targeting", response_class=HTMLResponse)
async def targeting_writeup_page(request: Request):
    """
    Backend tool for generating client targeting write-ups (Census demographics
    + FRED public banking data). Login-protected, same as /dashboard, /admin,
    and /client -- this is an internal sales research tool, not a customer or
    public-facing feature.
    """
    if not is_authenticated(request):
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

    return templates.TemplateResponse(request=request, name="targeting_writeup.html", context={})


@app.get("/admin/outreach", response_class=HTMLResponse)
async def outreach_page(request: Request):
    """
    Backend tool for scanning public business expansion signals, comparing
    local bank rates, and generating outreach emails. Login-protected, same
    pattern as the other backend pages -- not public/customer facing.
    """
    if not is_authenticated(request):
        return RedirectResponse(url="/login?error=Authentication+Required", status_code=303)

    return templates.TemplateResponse(request=request, name="outreach.html", context={})


@app.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(request: Request, business: Optional[str] = None, conn=Depends(get_db)):
    """
    Public unsubscribe endpoint, linked from every outreach email as
    required by the CAN-SPAM Act. No login required -- anyone with the
    link can opt out immediately, no confirmation loop.
    """
    if business:
        try:
            conn.execute(
                "INSERT INTO outreach_unsubscribes (business_identifier) VALUES (?) ON CONFLICT(business_identifier) DO NOTHING",
                (business,),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to record unsubscribe for {business}: {e}")

    return templates.TemplateResponse(
        request=request,
        name="unsubscribe.html",
        context={"business": business},
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


def build_checkout_session(
    conn: sqlite3.Connection,
    base_url: str,
    email: Optional[str] = None,
    business_name: Optional[str] = None,
) -> Optional[dict]:
    """
    Create a Stripe Checkout session and record it as a pending payment.
    Shared by the web checkout route and the voice bot's in-call closing flow.
    Returns the session_data dict (with a "url" to send the customer to), or
    None if Stripe isn't configured or the session couldn't be created.
    """
    if not stripe_ready():
        return None

    stripe_client = stripe.StripeClient(STRIPE_SECRET_KEY)
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
            return None

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
            return None
    except stripe.error.StripeError as exc:
        logger.warning("Stripe Checkout failed: code=%s param=%s", exc.code, exc.param)
        return None

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
    return session_data


@app.post("/api/checkout/create")
async def create_checkout_session(
    request: Request,
    email: Optional[str] = Form(default=None),
    business_name: Optional[str] = Form(default=None),
    conn=Depends(get_db),
):
    session_data = build_checkout_session(conn, normalize_base_url(request), email=email, business_name=business_name)
    if not session_data:
        return RedirectResponse(url="/?error=Unable+to+start+checkout", status_code=303)
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


@app.post("/twilio/voice/handle-dtmf-menu")
async def handle_dtmf_menu_entry():
    """
    Reached when the initial greeting's Gather times out with no speech or
    DTMF at all (e.g. bad connection, silent caller). Drops into a simple
    DTMF menu instead of looping the full greeting again -- looping back to
    /twilio/voice/incoming here is what caused calls to feel stuck repeating
    the same greeting over and over.
    """
    return xml_response(create_menu_fallback())


@app.post("/twilio/voice/process-input")
async def process_voice_input(
    SpeechResult: Optional[str] = Form(default=None),
    Digits: Optional[str] = Form(default=None),
    CallSid: Optional[str] = Form(default=None),
    From: Optional[str] = Form(default=None),
    request: Request = None,
    conn=Depends(get_db),
):
    """Process speech or DTMF input from caller and generate contextual response.

    This is also where the bot CLOSES: if the caller's state shows we're mid-close
    (awaiting a business name) or their speech signals buying intent, we create a
    real Stripe checkout session and text them the link right on the call.
    """
    user_input = (SpeechResult or Digits or "").strip()

    if not user_input:
        return xml_response(create_menu_fallback())

    call_state = get_call_state(CallSid)
    caller_phone = From or call_state.get("phone")
    if caller_phone:
        call_state["phone"] = caller_phone

    # If we already asked for the business name last turn, this turn's input IS the name.
    if call_state.get("awaiting_business_name"):
        business_name = extract_business_name(user_input) or "New BizStack Perks Customer"
        call_state["awaiting_business_name"] = False
        return xml_response(_close_and_send_checkout_link(conn, request, call_state, business_name, CallSid))

    # CLOSING PATH: caller signaled they're ready to buy right now. Handle this
    # BEFORE generating a full bot response, so we don't say "let's get you
    # going" twice (once from the bot's own reply, once from this flow).
    if detect_closing_intent(user_input):
        response = VoiceResponse()

        if not caller_phone:
            # Shouldn't normally happen (Twilio always sends From), but fail safe.
            response.say(
                "I couldn't get your callback number for the link -- let's set you up with a specialist instead.",
                voice=NATURAL_VOICE,
            )
            response.hangup()
            return xml_response(response)

        call_state["awaiting_business_name"] = True
        response.say(
            "Awesome, let's get you going! What's the business name I should put on the account?",
            voice=NATURAL_VOICE,
        )
        gather = Gather(
            input="speech",
            action="/twilio/voice/process-input",
            method="POST",
            timeout=8,
            speech_timeout="auto",
        )
        response.append(gather)
        return xml_response(response)

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

    # Prompt for a human callback if that's what they asked for
    if any(word in user_input.lower() for word in ["callback", "call back", "speak to someone", "human", "person"]):
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


def _close_and_send_checkout_link(
    conn: sqlite3.Connection,
    request: Request,
    call_state: dict,
    business_name: str,
    call_sid: Optional[str],
) -> VoiceResponse:
    """
    Create a real Stripe checkout session for this caller and text them the
    link. Also creates a tracked lead record so the sale shows up in the
    admin dashboard and analytics even before they pay.
    """
    response = VoiceResponse()
    caller_phone = call_state.get("phone")

    # Track this as a lead regardless of whether checkout succeeds, so every
    # in-call closing attempt is visible in the admin dashboard.
    try:
        conn.execute(
            """
            INSERT INTO leads (
                full_name, email, phone, application_type, requested_product,
                source, consent_text, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                business_name,
                f"voice-lead-{call_sid or 'unknown'}@bizstack-perks.local",
                caller_phone or "unknown",
                "business",
                "BizStack Perks Entry Plan",
                "voice-bot-outbound" if call_state.get("is_outbound") else "voice-bot-inbound",
                "Verbal consent given on recorded call; caller requested signup link by SMS.",
                "closing",
            ),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to record in-call lead: {e}")

    session_data = build_checkout_session(
        conn,
        normalize_base_url(request),
        business_name=business_name,
    )

    if not session_data:
        response.say(
            "I'm having trouble generating your signup link right now -- I'll have a specialist "
            "text it to you shortly instead. Thanks for your patience!",
            voice=NATURAL_VOICE,
        )
        response.hangup()
        return response

    checkout_url = session_data["url"]

    sms_sent = False
    if caller_phone and sms_manager.is_configured():
        try:
            sms_manager.client.messages.create(
                body=(
                    f"Thanks for calling BizStack Perks! Here's your secure signup link for "
                    f"{business_name}: {checkout_url}"
                ),
                from_=sms_manager.from_number,
                to=caller_phone,
            )
            sms_sent = True
        except Exception as e:
            logger.error(f"Failed to send closing SMS: {e}")

    if sms_sent:
        response.say(
            f"Perfect! I just texted the secure signup link to your number for {business_name}. "
            f"Just tap it, complete checkout, and you're all set. Thanks so much for calling "
            f"BizStack Perks!",
            voice=NATURAL_VOICE,
        )
    else:
        spoken_site = PUBLIC_BASE_URL or "our website"
        response.say(
            f"Your signup link is ready at {spoken_site}. I was not able to text it just now, "
            f"but a specialist will follow up with the link shortly. Thanks for calling BizStack Perks!",
            voice=NATURAL_VOICE,
        )

    response.hangup()
    return response


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

    client = create_telephony_client()

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


@app.post("/api/twilio/voice/outbound-sales")
async def trigger_outbound_sales_call(
    payload: OutboundCallRequest,
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    conn=Depends(get_db),
):
    """
    Place an outbound sales call that connects to the FULL conversational
    closing bot (not a static one-line message like /api/twilio/voice/outbound).
    The bot will greet the lead, answer questions, and attempt to close the
    sale live on the call -- same closing flow as inbound calls.
    """
    require_api_key_or_session(request, x_api_key)
    if not twilio_ready():
        raise HTTPException(status_code=503, detail="Twilio voice is not configured")

    client = create_telephony_client()

    try:
        call = client.calls.create(
            to=payload.to_number,
            from_=TWILIO_PHONE_NUMBER,
            twiml=create_outbound_sales_greeting(),
            status_callback=f"{normalize_base_url(request)}/twilio/voice/status",
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to place outbound sales call") from exc

    upsert_call_event(
        conn,
        call_sid=call.sid,
        direction="outbound-sales",
        call_status=getattr(call, "status", "queued"),
        from_number=TWILIO_PHONE_NUMBER,
        to_number=payload.to_number,
        message="Outbound sales call -- full conversational closing bot",
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
    customer_id: Optional[int] = Form(default=None),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    background_tasks: BackgroundTasks = None,
    conn=Depends(get_db),
):
    """Discover leads from Google Places by location + category.

    If customer_id is provided, discovered leads are assigned to that paying
    customer and will appear in their customer portal.
    """
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

    count = store_leads_to_db(conn, leads, customer_id=customer_id)

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


@app.post("/api/targeting/writeup")
async def create_targeting_writeup(
    state: str = Form(...),
    service_category: str = Form(...),
    county_fips: Optional[str] = Form(default=None),
    business_name: Optional[str] = Form(default=None),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """
    Generate a client-ready targeting write-up combining public Census
    demographic data and Federal Reserve (FRED) public banking/economic
    indicators for a given state/county and service category.

    Requires CENSUS_API_KEY and FRED_API_KEY to be configured; both are
    free, official government APIs (no scraping, no personal financial
    data). Returns structured data plus a ready-to-send narrative text.
    """
    require_api_key_or_session(request, x_api_key)

    if not CENSUS_API_KEY:
        raise HTTPException(status_code=503, detail="CENSUS_API_KEY is not configured")
    if not FRED_API_KEY:
        raise HTTPException(status_code=503, detail="FRED_API_KEY is not configured")

    try:
        writeup = await generate_targeting_writeup(
            state=state,
            service_category=service_category,
            census_api_key=CENSUS_API_KEY,
            fred_api_key=FRED_API_KEY,
            county_fips=county_fips,
            business_name=business_name,
        )
    except Exception as e:
        logger.error(f"Targeting write-up generation failed: {e}")
        raise HTTPException(status_code=502, detail="Unable to generate write-up right now") from e

    return writeup


# ============================================================================
# NEW: Business Signal Scanning + Outreach Generation
# ============================================================================


@app.post("/api/signals/scan")
async def scan_business_signals(
    location: str = Form(...),
    industry: Optional[str] = Form(default=None),
    days_back: int = Form(default=30),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """
    Scan public news for businesses showing expansion/loan-seeking signals
    in a given location. Uses NewsAPI (free tier) -- a legitimate public
    news aggregator, not scraping of any bank or private data source.
    """
    require_api_key_or_session(request, x_api_key)

    if not NEWSAPI_KEY:
        raise HTTPException(status_code=503, detail="NEWSAPI_KEY is not configured")

    scanner = NewsSignalScanner(NEWSAPI_KEY)
    try:
        signals = await scanner.scan_for_signals(location=location, industry=industry, days_back=days_back)
    except Exception as e:
        logger.error(f"Business signal scan failed: {e}")
        raise HTTPException(status_code=502, detail="Unable to scan for signals right now") from e

    deduped = deduplicate_signals(signals)
    return {"location": location, "industry": industry, "signal_count": len(deduped), "signals": [s.dict() for s in deduped]}


@app.get("/api/bank-rates")
async def get_bank_rates(
    region: Optional[str] = None,
    loan_type: Optional[str] = None,
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """
    Get curated local bank loan rates for a region/loan type. This reads
    from a manually-maintained rate sheet (bank_rates.json) rather than
    scraping bank websites -- see local_bank_rates.py for why.
    """
    require_api_key_or_session(request, x_api_key)

    rates = get_best_rates_for_region(region=region, loan_type=loan_type, limit=10)
    stale = check_rate_staleness()

    return {
        "rates": format_rates_for_display(rates),
        "stale_entries_needing_review": len(stale),
    }


@app.post("/api/outreach/generate")
async def generate_outreach(
    business_name: str = Form(...),
    signal_type: str = Form(default="news"),
    signal_summary: str = Form(...),
    source_name: str = Form(default="Public source"),
    location: Optional[str] = Form(default=None),
    region: Optional[str] = Form(default=None),
    loan_type: Optional[str] = Form(default=None),
    sender_name: str = Form(...),
    request: Request = None,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """
    Generate a personalized, CAN-SPAM-compliant outreach email for a
    business showing a public expansion/loan-seeking signal, referencing
    real local bank rate comparisons.

    Requires SENDER_PHYSICAL_ADDRESS to be configured -- CAN-SPAM requires
    a real physical mailing address in every commercial email.
    """
    require_api_key_or_session(request, x_api_key)

    if not SENDER_PHYSICAL_ADDRESS:
        raise HTTPException(
            status_code=503,
            detail="SENDER_PHYSICAL_ADDRESS is not configured (required by CAN-SPAM for commercial email)",
        )

    from business_signals import BusinessSignal

    signal = BusinessSignal(
        business_name=business_name,
        signal_type=signal_type,
        signal_summary=signal_summary,
        source_name=source_name,
        location=location,
        confidence_score=0.6,
    )

    base_url = normalize_base_url(request)
    unsubscribe_url = f"{base_url}/unsubscribe?business={business_name.replace(' ', '-').lower()}"

    email = generate_outreach_email(
        signal=signal,
        sender_name=sender_name,
        sender_company=SENDER_COMPANY_NAME,
        sender_physical_address=SENDER_PHYSICAL_ADDRESS,
        unsubscribe_url=unsubscribe_url,
        region=region,
        loan_type=loan_type,
    )

    return email


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


# ============================================================================
# Customer Portal (separate from admin auth -- customers only ever see their
# own data, never the admin dashboard/admin/client registry views)
# ============================================================================

PORTAL_SESSION_COOKIE = "portal_session_token"


def get_portal_customer(request: Request, conn) -> Optional[sqlite3.Row]:
    token = request.cookies.get(PORTAL_SESSION_COOKIE)
    if not token:
        return None
    return get_customer_by_session_token(conn, token)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: Optional[str] = None, created: bool = False):
    """
    Public, free, self-service signup for the customer portal. No payment
    required to create an account -- billing can be added later via
    /portal/billing. This exists so customers (and testers) don't need to
    complete a Stripe checkout just to create a portal login.
    """
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={"error": error, "created": created, "consent_text": LEAD_CONSENT_TEXT},
    )


@app.post("/signup")
async def process_signup(
    request: Request,
    business_name: str = Form(...),
    email: Optional[str] = Form(default=None),
    phone: Optional[str] = Form(default=None),
    consent: Optional[str] = Form(default=None),
    conn=Depends(get_db),
):
    """Create a free customer portal account -- no Stripe checkout required."""
    email = (email or "").strip().lower() or None
    phone = (phone or "").strip() or None

    if consent != "accepted":
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={"error": "Please accept the consent notice to continue.", "created": False, "consent_text": LEAD_CONSENT_TEXT},
        )

    if not email and not phone:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={"error": "Provide at least an email or phone number.", "created": False, "consent_text": LEAD_CONSENT_TEXT},
        )

    if phone and not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={"error": "Enter a valid phone number in +1XXXXXXXXXX format.", "created": False, "consent_text": LEAD_CONSENT_TEXT},
        )

    customer_id = provision_customer_from_checkout(
        conn,
        email=email,
        business_name=business_name.strip()[:120],
        stripe_customer_id=None,
        phone=phone,
    )

    if phone and customer_id:
        link_phone_to_customer(conn, customer_id, phone)

    if not customer_id:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={"error": "Unable to create account right now. Please try again.", "created": False, "consent_text": LEAD_CONSENT_TEXT},
        )

    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={"error": None, "created": True, "consent_text": LEAD_CONSENT_TEXT},
    )


@app.get("/portal/login", response_class=HTMLResponse)
async def portal_login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request=request,
        name="portal_login.html",
        context={"error": error, "code_sent": False, "identifier": "", "channel": "phone"},
    )


@app.post("/portal/request-code")
async def portal_request_code(
    request: Request,
    identifier: str = Form(...),
    channel: str = Form(default="phone"),
    conn=Depends(get_db),
):
    """Send a login code via phone (SMS) or email, based on the selected channel."""
    identifier = identifier.strip()

    if channel == "email":
        if "@" not in identifier or "." not in identifier:
            return templates.TemplateResponse(
                request=request,
                name="portal_login.html",
                context={"error": "Enter a valid email address.", "code_sent": False, "identifier": "", "channel": "email"},
            )
        identifier = identifier.lower()
        customer = get_customer_by_email(conn, identifier)
        if not customer:
            # Don't reveal whether the account exists (avoid account enumeration).
            logger.info("Portal login requested for unknown email (no account created)")
        else:
            code = generate_otp(identifier)
            if email_configured():
                if not send_portal_login_code(identifier, code):
                    logger.error("Failed to send portal OTP email to %s", identifier)
            else:
                # No SMTP configured (e.g. local dev) -- log the code so the flow is testable.
                logger.warning(f"Email not configured; portal OTP for {identifier} is: {code}")
    else:
        channel = "phone"
        if not re.fullmatch(r"\+[1-9]\d{7,14}", identifier):
            return templates.TemplateResponse(
                request=request,
                name="portal_login.html",
                context={"error": "Enter a valid phone number in +1XXXXXXXXXX format.", "code_sent": False, "identifier": "", "channel": "phone"},
            )

        customer = get_customer_by_phone(conn, identifier)
        if not customer:
            # Don't reveal whether the phone number exists (avoid account enumeration).
            # Always show the "code sent" screen either way.
            logger.info("Portal login requested for unknown phone (no account created)")
        else:
            code = generate_otp(identifier)
            if sms_manager.is_configured():
                try:
                    sms_manager.client.messages.create(
                        body=f"Your BizStack Perks login code is {code}. It expires in 10 minutes.",
                        from_=sms_manager.from_number,
                        to=identifier,
                    )
                except Exception as e:
                    logger.error(f"Failed to send portal OTP SMS: {e}")
            else:
                # No Twilio configured (e.g. local dev) -- log the code so the flow is testable.
                logger.warning(f"SMS not configured; portal OTP for {identifier} is: {code}")

    return templates.TemplateResponse(
        request=request,
        name="portal_login.html",
        context={"error": None, "code_sent": True, "identifier": identifier, "channel": channel},
    )


@app.post("/portal/verify")
async def portal_verify_code(
    request: Request,
    identifier: str = Form(...),
    code: str = Form(...),
    channel: str = Form(default="phone"),
    conn=Depends(get_db),
):
    identifier = identifier.strip()
    if channel == "email":
        identifier = identifier.lower()

    if not verify_otp(identifier, code):
        return templates.TemplateResponse(
            request=request,
            name="portal_login.html",
            context={"error": "That code is invalid or expired. Please try again.", "code_sent": False, "identifier": "", "channel": channel},
        )

    customer = get_customer_by_email(conn, identifier) if channel == "email" else get_customer_by_phone(conn, identifier)
    if not customer:
        return templates.TemplateResponse(
            request=request,
            name="portal_login.html",
            context={"error": "We couldn't find an account for that login.", "code_sent": False, "identifier": "", "channel": channel},
        )

    token = create_portal_session_token(conn, customer["id"])
    response = RedirectResponse(url="/portal", status_code=303)
    response.set_cookie(
        key=PORTAL_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=normalize_base_url(request).startswith("https://"),
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return response


@app.get("/portal/logout")
async def portal_logout(request: Request, conn=Depends(get_db)):
    customer = get_portal_customer(request, conn)
    if customer:
        clear_portal_session(conn, customer["id"])
    response = RedirectResponse(url="/portal/login", status_code=303)
    response.delete_cookie(PORTAL_SESSION_COOKIE)
    return response


@app.get("/portal", response_class=HTMLResponse)
async def customer_portal(request: Request, conn=Depends(get_db)):
    customer = get_portal_customer(request, conn)
    if not customer:
        return RedirectResponse(url="/portal/login", status_code=303)

    leads = get_customer_leads(conn, customer["id"])
    return templates.TemplateResponse(
        request=request,
        name="portal_dashboard.html",
        context={"customer": customer, "leads": leads},
    )


@app.post("/portal/billing")
async def portal_billing_redirect(request: Request, conn=Depends(get_db)):
    """Generate a Stripe Customer Portal session so the customer can manage
    their own billing (update card, view invoices, cancel) without ever
    touching the admin backend."""
    customer = get_portal_customer(request, conn)
    if not customer:
        return RedirectResponse(url="/portal/login", status_code=303)

    if not stripe_ready() or not customer["stripe_customer_id"]:
        return RedirectResponse(url="/portal?error=Billing+portal+is+not+available+yet", status_code=303)

    try:
        stripe_client = stripe.StripeClient(STRIPE_SECRET_KEY)
        portal_session = stripe_client.billing_portal.sessions.create(
            params={
                "customer": customer["stripe_customer_id"],
                "return_url": f"{normalize_base_url(request)}/portal",
            }
        )
        session_data = portal_session.to_dict() if hasattr(portal_session, "to_dict") else portal_session
        return RedirectResponse(url=session_data["url"], status_code=303)
    except Exception as exc:
        logger.warning(f"Stripe billing portal session error: {exc}")
        return RedirectResponse(url="/portal?error=Unable+to+open+billing+portal", status_code=303)
