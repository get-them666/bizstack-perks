"""
Advanced Twilio voice bot with OpenAI integration for live conversation.
Handles inbound and outbound calls with real Q&A, sounds like a knowledgeable
human rep (not a robotic menu tree), carries multi-turn conversation memory
per call, and can CLOSE a sale live on the call by detecting buying intent
and triggering a real Stripe checkout link sent by SMS.
"""

import os
import re
import logging
import time
from typing import Optional, List, Dict
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import httpx

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Natural-sounding neural voice instead of the robotic classic "alice" voice.
# Polly.Joanna-Neural / Polly.Matthew-Neural require <Say> to specify them directly;
# Twilio will use Amazon Polly's neural TTS engine automatically for these voices.
NATURAL_VOICE = os.getenv("TWILIO_VOICE", "Polly.Joanna-Neural")
NATURAL_LANGUAGE = "en-US"

OFFER_PRICE_DISPLAY = os.getenv("OFFER_PRICE_DISPLAY", "$49 / month")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "bizstack-perks.com")

# ============================================================================
# In-memory per-call conversation state.
# Keyed by Twilio CallSid so the bot remembers what was already said on this
# call. This is process-local (fine for a single instance); it self-expires
# so memory doesn't grow unbounded across many calls.
# ============================================================================
_CONVERSATIONS: Dict[str, Dict] = {}
_CONVERSATION_TTL_SECONDS = 60 * 30  # 30 minutes

# Phrases that signal the caller is ready to buy right now. Checked in
# addition to whatever the LLM says, so closing works even in fallback mode
# (no OpenAI key) or if the model doesn't literally say "yes."
CLOSING_INTENT_PHRASES = [
    "sign me up", "sign up", "let's do it", "lets do it", "i'm interested", "im interested",
    "i want to start", "how do i start", "how do i sign up", "get me started", "get started",
    "yes let's", "yes lets", "okay let's", "okay lets", "sounds good let's", "i'll take it",
    "ill take it", "where do i pay", "send me the link", "text me the link", "i'm in", "im in",
    "let's go", "lets go", "yeah let's try", "yeah lets try", "set me up",
]


def detect_closing_intent(user_input: str) -> bool:
    """Check whether the caller's speech signals they're ready to buy now."""
    text = user_input.lower().strip()
    return any(phrase in text for phrase in CLOSING_INTENT_PHRASES)


def _prune_stale_conversations() -> None:
    now = time.time()
    stale = [sid for sid, data in _CONVERSATIONS.items() if now - data["updated_at"] > _CONVERSATION_TTL_SECONDS]
    for sid in stale:
        _CONVERSATIONS.pop(sid, None)


def get_conversation_history(call_sid: Optional[str]) -> List[Dict[str, str]]:
    if not call_sid:
        return []
    entry = _CONVERSATIONS.get(call_sid)
    return list(entry["messages"]) if entry else []


def append_conversation_turn(call_sid: Optional[str], role: str, content: str) -> None:
    if not call_sid:
        return
    _prune_stale_conversations()
    entry = _CONVERSATIONS.setdefault(call_sid, {"messages": [], "updated_at": time.time()})
    entry["messages"].append({"role": role, "content": content})
    entry["updated_at"] = time.time()
    # Cap history length so prompts don't grow unbounded on very long calls.
    if len(entry["messages"]) > 20:
        entry["messages"] = entry["messages"][-20:]


def clear_conversation(call_sid: Optional[str]) -> None:
    if call_sid:
        _CONVERSATIONS.pop(call_sid, None)


def get_call_state(call_sid: Optional[str]) -> Dict:
    """Get/create a small state dict for this call (e.g. captured business name)."""
    if not call_sid:
        return {}
    _prune_stale_conversations()
    entry = _CONVERSATIONS.setdefault(call_sid, {"messages": [], "updated_at": time.time()})
    return entry.setdefault("state", {})


def extract_business_name(user_input: str) -> Optional[str]:
    """
    Best-effort extraction of a business name from a spoken reply like
    "it's called Joe's Plumbing" or "Joe's Plumbing". Not perfect NLP --
    just strips common filler phrases so the captured name is usable.
    """
    text = user_input.strip()
    if not text:
        return None

    fillers = [
        r"^(it'?s\s+called\s+)", r"^(it'?s\s+)", r"^(the name is\s+)", r"^(my business is\s+)",
        r"^(we'?re\s+called\s+)", r"^(called\s+)", r"^(um+,?\s*)", r"^(uh+,?\s*)",
    ]
    for pattern in fillers:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    text = text.strip().strip(".").strip()
    return text[:120] if text else None


# ============================================================================
# System prompt: deep domain knowledge + site knowledge + compliance guardrails
# + closing behavior
# ============================================================================

SYSTEM_PROMPT = f"""You are Sam, a knowledgeable, warm, and conversational phone representative for
BizStack Perks. You are NOT a stiff IVR menu bot — you talk like a sharp, friendly human expert who
genuinely enjoys explaining things clearly. Use contractions, vary your sentence structure, and react
naturally to what the caller says (acknowledge, then answer). Keep answers to 2-4 sentences since this
is a phone call — long monologues are bad on voice. Never say "As an AI" or mention that you are a bot
or a language model. Never sound scripted or repeat the same stock phrase twice in one call.

=== ABOUT BIZSTACK PERKS (know this cold — this is the real site, page by page) ===
BizStack Perks is a lead-generation and monetization platform for local service businesses
(contractors, financial advisors, loan brokers, and similar). Here's the full site, exactly as it's
built, so you can walk any caller through it like you use it every day:

- Homepage ({PUBLIC_BASE_URL}/): the main landing page. Has a pricing section, an FAQ, and a checkout
  form right on the page — visitors type their business name and email and click "Start checkout" to
  pay via Stripe. There's also a "Call or contact" button and links to "Business financing" and
  "Consumer credit" application forms, "Partner offers" (affiliates), and the dashboard login.
- Apply page (/apply?application_type=business or ?application_type=consumer): this is the lead-intake
  form. A visitor enters their full name, email, phone (in +1 format), what they're interested in (like
  "business line of credit"), and optionally a requested amount. They must check a consent box before
  submitting — we never accept Social Security numbers, bank credentials, or card numbers on this form,
  and the page says so explicitly. Submitting sends them to a "Request received" confirmation page with
  a reference number.
- Partner offers page (/affiliates): lists approved affiliate partner links. Every link says to review
  the partner's own terms before applying — BizStack Perks doesn't control what partners offer.
- Customer portal login (/portal/login): this is for EXISTING PAYING CUSTOMERS — they log in with just
  their phone number and a text-message verification code (no password to remember). Once in, they see
  their own leads and can manage billing (update card, view invoices, cancel) through a secure Stripe
  billing portal link. If a caller says they're already a customer and want to see their leads or manage
  billing, point them here — NOT to the owner's login below.
- Login page (/login): this is ONLY for the business owner/staff running BizStack Perks internally — not
  for customers or the public. Don't direct callers here; if someone asks about the owner's dashboard,
  it's not something you'd walk a caller through.
- Dashboard (/dashboard, login required): lets the owner add new company profiles (name, annual revenue,
  credit risk rating) and see a grid of existing profiles color-coded by risk. Links out to the Admin
  workspace and Client registry.
- Client registry (/client, login required): a searchable, exportable table of every company profile —
  you can filter live and export the whole thing to CSV with one click.
- Admin workspace (/admin, login required): the single view that shows everything at once — opt-in lead
  requests, Stripe checkout activity (with amounts and status), Twilio voice call history, and company
  profiles. This is the owner's command center.
- Checkout success / cancel pages: after Stripe checkout, buyers land on a simple confirmation or
  cancellation page. Importantly, the *actual* payment confirmation always happens server-side via a
  verified Stripe webhook, not just the redirect — so it's tamper-resistant.
- Voice (this call!): inbound calls hit our Twilio Voice line and reach me. There's also a backend API
  that can trigger real outbound calls to a phone number with a custom message.

- Lead discovery (behind the scenes): automatically finds businesses and prospects in a target area and
  category using Google Places data and public Census demographic data, so customers can find
  underserved, high-opportunity neighborhoods instead of guessing.
- SMS automation: sends compliant, opt-out-respecting text follow-ups to leads automatically, and
  handles inbound replies (like STOP to unsubscribe) correctly.
- Analytics: the admin workspace and a dedicated analytics API show which locations and lead sources
  convert best, so ad spend goes where it actually works.
- Affiliate program: partners can refer business and earn tracked commissions, paid out in batches.
- Payments: checkout and billing run on Stripe, so it's PCI-compliant and secure — BizStack Perks never
  stores raw card numbers; a webhook confirms every payment on the server.
- Pricing: the entry plan is {OFFER_PRICE_DISPLAY}. It includes lead discovery, SMS notifications, and
  the analytics dashboard.
- Getting started: sign up on the website ({PUBLIC_BASE_URL}), pick a service category and location, and
  leads start flowing into the dashboard. A specialist can also walk a new customer through it live.

=== YOUR AREAS OF EXPERTISE (answer confidently and clearly, like a pro) ===
You are genuinely well-versed in:
- Banking & business fundamentals: how businesses price services, unit economics, customer acquisition
  cost vs. lifetime value, cash flow basics, what makes a lead "qualified," margins.
- Economics: supply and demand, how local market saturation affects pricing and lead value, basic
  inflation/interest-rate effects on borrowing costs.
- Math relevant to the business: percentages, ROI and payback period calculations, how commission splits
  work, simple amortization concepts (how loan payments break down into principal and interest over time).
- Web development & payments: what an API is in plain English, how Stripe Checkout works end-to-end
  (customer clicks pay, Stripe hosts the secure payment page, a webhook confirms payment server-side),
  what a webhook is, why PCI compliance matters, basics of how a lead-capture form and CRM connect.
- Loans & credit cards: general concepts like APR vs. interest rate, secured vs. unsecured credit,
  revolving vs. installment debt, how a line of credit differs from a term loan, typical use cases for
  each.
- Credit worthiness: the general factors that make up a credit score (payment history, credit
  utilization, length of credit history, credit mix, new credit inquiries) and why lenders look at them.

=== CRITICAL GUARDRAILS — NEVER CROSS THESE ===
1. You are educational, not a licensed lender, broker, or financial advisor. Never quote a specific
   interest rate, APR, credit limit, or loan amount for the caller personally. Never say "you would
   qualify for X" or guarantee any approval, rate, or credit decision.
2. If someone asks for personalized financial, credit, tax, or legal advice about their own situation,
   give the general concept, then clearly hand off: "For your specific numbers, I'll connect you with a
   licensed specialist who can look at your actual application — want me to set that up?"
3. Never ask for or accept full credit card numbers, SSNs, or full bank account numbers over this call.
   If someone starts to give you sensitive numbers, politely stop them and redirect to the secure web
   form or a callback.
4. Stay honest about BizStack Perks — don't invent features, guarantees, or numbers that weren't given
   to you above.

=== YOU ARE A CLOSER — THIS IS THE MOST IMPORTANT PART OF YOUR JOB ===
Your #1 goal on every call is to get the caller signed up and paying TODAY, on this call, without
handing them off to a human unless they explicitly ask for one or the conversation genuinely requires
it (a guardrail situation above). You are not just an FAQ bot — you are a real salesperson.

- Be proactive: after answering 1-2 questions, ASK for the close. "Want me to get you set up right now?
  I can text you the signup link before we even hang up." Don't wait for them to bring it up.
- The moment the caller says anything that sounds like a yes — "sure", "let's do it", "sign me up",
  "okay I'm interested", "how do I start", "yeah let's try it" — treat that as a green light to close.
  Ask for their business name if you don't already have it: "Awesome, let's get you going — what's the
  business name I should put on the account?" Once you have a name (or if they skip it), confirm:
  "Perfect — I'm texting you the secure signup link right now to this number. You'll get a text in a
  few seconds." The system automatically detects this closing language and sends the real Stripe
  checkout link by SMS behind the scenes, so speak naturally as if you're actually doing it live.
- Do NOT default to "I'll set up a callback with a specialist" as your first move — that's a fallback
  for when someone has a complex personalized question (see guardrails above), not your default close.
  You can close simple {OFFER_PRICE_DISPLAY} signups yourself, live, on the call.
- If they hesitate or ask "is this legit" / "how do I know this works", reassure them briefly (secure
  Stripe checkout, no contract, cancel anytime) and ask again if they want to start.
- Only offer a human callback when: they explicitly ask for a person, the guardrails above require it
  (personalized financial/credit/legal advice), or they say no / not interested (in which case, thank
  them warmly and let the call end naturally — don't be pushy after a clear no).

=== CONVERSATION STYLE ===
- Sound like a real person having a normal phone conversation, not reading a script.
- If they ask something totally unrelated (weather, jokes, etc.), respond briefly and warmly, then
  gently steer back: "Ha, I'm mostly the BizStack Perks guy, but — anything about the platform I can
  help with?"
"""


class VoiceBotResponseGenerator:
    """Generate contextual, natural-sounding voice bot responses using OpenAI or fallback rules."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or OPENAI_API_KEY
        self.has_openai = bool(self.api_key)

    async def generate_response(
        self,
        user_input: str,
        call_sid: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> str:
        """
        Generate a voice bot response to user input, using conversation history
        for the call (if call_sid is provided) so multi-turn dialogue feels natural.
        Falls back to rule-based responses if OpenAI is unavailable or errors out.
        """
        if self.has_openai:
            reply = await self._openai_response(user_input, call_sid, context)
        else:
            reply = self._fallback_response(user_input, context)

        if call_sid:
            append_conversation_turn(call_sid, "user", user_input)
            append_conversation_turn(call_sid, "assistant", reply)

        return reply

    async def _openai_response(
        self, user_input: str, call_sid: Optional[str] = None, context: Optional[dict] = None
    ) -> str:
        """Generate a response using OpenAI, including prior turns of this call for continuity."""
        try:
            history = get_conversation_history(call_sid)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_input})

            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json={
                        "model": OPENAI_MODEL,
                        "messages": messages,
                        "temperature": 0.8,
                        "max_tokens": 180,
                    },
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return self._fallback_response(user_input, context)

    @staticmethod
    def _fallback_response(user_input: str, context: Optional[dict] = None) -> str:
        """
        Rule-based responses when OpenAI is unavailable. Written to sound like a
        real person talking, not a script — used only as a safety net.
        """
        text = user_input.lower().strip()

        if detect_closing_intent(user_input):
            return (
                "Awesome, let's get you going! What's the business name I should put on the account? "
                "I'll text you the secure signup link right after."
            )

        if any(word in text for word in ["price", "cost", "how much", "fee", "monthly"]):
            return (
                f"Good question — the entry plan runs {OFFER_PRICE_DISPLAY}, and that covers lead "
                f"discovery, SMS follow-ups, and the analytics dashboard. No hidden setup fees, no "
                f"contract. Want me to text you the signup link right now so you can get started?"
            )

        if any(word in text for word in ["credit score", "fico", "credit worth"]):
            return (
                "Credit scores mostly come down to five things: payment history, how much of your "
                "available credit you're using, how long you've had credit, the mix of account types, "
                "and recent inquiries. I can't speak to your specific score, but a specialist can walk "
                "through your situation if that's helpful."
            )

        if any(word in text for word in ["apr", "interest rate", "loan rate"]):
            return (
                "APR is basically the true yearly cost of borrowing, folding in the interest rate plus "
                "most fees, so it's the number you want to compare across offers. I can't quote you a "
                "specific rate on this call, but I can get a specialist to go over real numbers with you."
            )

        if any(word in text for word in ["stripe", "payment", "checkout", "webhook"]):
            return (
                "We run all payments through Stripe, so it's fully PCI-compliant — we never touch raw "
                "card numbers. When someone checks out, Stripe hosts the secure payment page, and a "
                "webhook confirms the payment on our end automatically. Pretty seamless."
            )

        if any(word in text for word in ["what", "features", "included", "get", "do you"]):
            return (
                "Here's the short version: we find leads in your area using Google Places and Census "
                "data, follow up automatically by text, and show you exactly which areas convert best. "
                "You focus on closing the deal, we handle finding the opportunity. Want me to get you "
                "set up right now?"
            )

        if any(word in text for word in ["how", "works", "process", "start", "sign up"]):
            return (
                "It's pretty simple — you sign up, tell us your service category and target location, "
                "and leads start showing up in your dashboard. Want me to text you the signup link right "
                "now so you can get started today?"
            )

        if any(word in text for word in ["no", "not interested", "not now", "maybe later"]):
            return "No worries at all! Thanks so much for calling, and feel free to reach out anytime."

        if any(word in text for word in ["callback", "call back", "speak to someone", "human", "person"]):
            return "You got it — I'll line up a callback with one of our specialists within 24 hours."

        return (
            "Happy to help with that — could you tell me a bit more about what you're looking for? "
            "I can talk pricing, how the platform works, or get you signed up right now."
        )


def create_voice_greeting() -> str:
    """Create an initial greeting TwiML with a natural neural voice."""
    response = VoiceResponse()
    response.say(
        "Hey there, thanks for calling BizStack Perks! I'm Sam. We help service businesses find and "
        "close more qualified leads — what can I help you with today?",
        voice=NATURAL_VOICE,
        language=NATURAL_LANGUAGE,
    )

    gather = Gather(
        input="speech",
        action="/twilio/voice/process-input",
        method="POST",
        speech_timeout="auto",
        timeout=10,
        language=NATURAL_LANGUAGE,
        hints="pricing, features, callback, credit score, APR, Stripe, loans, how it works, sign me up, yes, no",
    )
    response.append(gather)

    # Fallback if no input
    response.say("Sorry, I didn't quite catch that — let's try again.", voice=NATURAL_VOICE)
    response.redirect("/twilio/voice/incoming", method="POST")

    return str(response)


def create_outbound_sales_greeting() -> str:
    """
    Create the greeting used for OUTBOUND sales calls (e.g. calling a lead
    proactively) -- slightly different framing since we initiated the call.
    """
    response = VoiceResponse()
    response.say(
        "Hi, this is Sam calling from BizStack Perks — we help local service businesses get a steady "
        "stream of qualified leads, and I wanted to give you a quick call about it. Do you have a "
        "minute?",
        voice=NATURAL_VOICE,
        language=NATURAL_LANGUAGE,
    )

    gather = Gather(
        input="speech",
        action="/twilio/voice/process-input",
        method="POST",
        speech_timeout="auto",
        timeout=10,
        language=NATURAL_LANGUAGE,
        hints="pricing, features, sign me up, yes, no, not interested",
    )
    response.append(gather)

    response.say("Sorry, I didn't quite catch that — let's try again.", voice=NATURAL_VOICE)
    response.hangup()

    return str(response)


def create_callback_confirmation(phone: str, name: Optional[str] = None) -> str:
    """Create confirmation TwiML for callback setup."""
    response = VoiceResponse()
    response.say(
        "You're all set — a specialist will reach out within 24 hours. Thanks so much for calling "
        "BizStack Perks, take care!",
        voice=NATURAL_VOICE,
    )
    response.hangup()
    return str(response)


def create_information_response(topic: str) -> str:
    """Create an informational response with follow-up prompt."""
    responses = {
        "pricing": (
            f"The entry plan is {OFFER_PRICE_DISPLAY} — that includes lead discovery, SMS notifications, "
            "conversion tracking, and the analytics dashboard. No setup fees."
        ),
        "features": (
            "You get our lead discovery engine covering Google Local Services and Census data, SMS "
            "follow-up automation, real-time lead alerts, and a full analytics dashboard — everything "
            "you need to scale."
        ),
        "free_trial": (
            f"We offer a 14-day free trial, no credit card required. You can sign up right at "
            f"{PUBLIC_BASE_URL} slash trial."
        ),
    }

    message = responses.get(topic, "I'm not totally sure on that one — want me to get you a callback with more detail?")

    response = VoiceResponse()
    response.say(message, voice=NATURAL_VOICE, language=NATURAL_LANGUAGE)
    response.say("Anything else I can help with, or does that cover it?", voice=NATURAL_VOICE)

    gather = Gather(
        input="speech dtmf",
        action="/twilio/voice/process-input",
        method="POST",
        timeout=5,
        speech_timeout="auto",
    )
    response.append(gather)

    return str(response)


def create_menu_fallback() -> str:
    """Create fallback menu with DTMF options for when speech isn't understood."""
    response = VoiceResponse()
    response.say(
        "Sorry, I'm having a little trouble hearing you clearly — let me give you a few options instead.",
        voice=NATURAL_VOICE,
    )

    gather = Gather(
        input="dtmf",
        action="/twilio/voice/handle-dtmf",
        method="POST",
        timeout=5,
        num_digits=1,
    )
    gather.say(
        "Press 1 for pricing. Press 2 to hear about features. Press 3 to request a callback. "
        "Press 4 to visit our website.",
        voice=NATURAL_VOICE,
    )
    response.append(gather)

    response.say("Thanks for calling, goodbye!", voice=NATURAL_VOICE)
    response.hangup()

    return str(response)
