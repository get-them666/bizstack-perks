import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

import stripe
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

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

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", os.getenv("TWILIO_NUMBER", ""))

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
    response.say(message, voice="alice")
    response.hangup()
    return str(response)


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
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="BizStack Perks", lifespan=lifespan)


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

    try:
        session = stripe_client.checkout.sessions.create(
            params={
                "mode": "payment",
                "line_items": [{"price": PRICE_ID, "quantity": 1}],
                "customer_email": (email or "").strip() or None,
                "success_url": f"{base_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base_url}/checkout/cancel",
                "metadata": metadata,
            },
        )
    except stripe.error.StripeError:
        return RedirectResponse(url="/?error=Unable+to+start+checkout", status_code=303)

    record_checkout_session(
        conn,
        {
            "id": session["id"],
            "customer": session.get("customer"),
            "payment_intent": session.get("payment_intent"),
            "subscription": session.get("subscription"),
            "payment_status": session.get("payment_status", "unpaid"),
            "status": session.get("status", "open"),
            "amount_total": session.get("amount_total"),
            "currency": session.get("currency"),
            "customer_email": (email or "").strip() or None,
            "customer_details": {"email": (email or "").strip() or None},
        },
    )
    return RedirectResponse(url=session["url"], status_code=303)


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
    response = VoiceResponse()
    gather = Gather(
        input="speech dtmf",
        action="/twilio/voice/menu",
        method="POST",
        num_digits=1,
        timeout=5,
        speech_timeout="auto",
    )
    gather.say(
        "Welcome to BizStack Perks. Press 1 to hear the current offer, or press 2 to request a callback.",
        voice="alice",
    )
    response.append(gather)
    response.say("Sorry, we did not receive a selection. Goodbye.", voice="alice")
    response.hangup()
    return xml_response(response)


@app.post("/twilio/voice/menu")
@app.post("/twilio/handle-input")
async def handle_input(Digits: Optional[str] = Form(default=None), SpeechResult: Optional[str] = Form(default=None)):
    response = VoiceResponse()
    choice = (Digits or (SpeechResult or "")).strip().lower()

    if choice == "1" or "price" in choice or "offer" in choice:
        response.say(
            f"Our featured BizStack Perks plan starts at {OFFER_PRICE_DISPLAY}. "
            "Complete checkout on the website to activate onboarding.",
            voice="alice",
        )
    elif choice == "2" or "callback" in choice or "agent" in choice or "help" in choice:
        response.say(
            "Thanks. Please use the website contact options or trigger an outbound call from your dashboard to continue.",
            voice="alice",
        )
    else:
        response.say("I did not understand that selection. Please call back and try again.", voice="alice")

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
