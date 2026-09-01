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
BizStack Perks is a lead-generation, client intake, and monetization platform for financial services
businesses (loan brokers, credit consultants, mortgage advisors, and similar). Here's the full site:

- Homepage ({PUBLIC_BASE_URL}/): main landing page with pricing, FAQ, and checkout form. Visitors enter
  their business name and email and click "Start checkout" to pay via Stripe. Links to business
  financing and consumer credit application forms, affiliate offers, and the dashboard login.
- Apply page (/apply?application_type=business or ?application_type=consumer): lead-intake form where
  a prospect enters name, email, phone, what they're interested in, and optionally a requested amount.
  Must check consent before submitting. We do NOT accept SSNs, bank credentials, or card numbers here.
- Client Intake Pipeline (/admin/pipeline/new): admin-only intake for existing clients — full financial
  profile form (product type, credit range, income, collateral, etc.) that auto-pulls live Fed rate
  data and Census demographics and generates a personalized email draft for the advisor to review.
- Pipeline Queue (/admin/pipeline): admin view of all client intake drafts, their status, and links
  to review/approve each draft.
- Partner offers page (/affiliates): approved affiliate partner links.
- Customer portal login (/portal/login): for EXISTING PAYING CUSTOMERS — login via phone or email OTP
  (6-digit code, no password). Once in, they see their own leads and can manage billing via Stripe.
- Login page (/login): OWNER/STAFF ONLY — not for customers or the public.
- Dashboard (/dashboard): add company profiles, see risk-coded grid, links to Admin and Client.
- Client registry (/client): searchable, exportable table of all company profiles.
- Admin workspace (/admin): everything at once — leads, payments, calls, company profiles, customers.
- Checkout success / cancel pages: simple confirmation after Stripe checkout.
- Voice (this call): inbound calls hit our Twilio Voice line. Backend can also trigger outbound calls.
- Lead discovery: automatically finds prospects using Google Places and Census demographics.
- SMS automation: compliant opt-out-respecting text follow-ups, handles STOP replies.
- Analytics: shows which locations and lead sources convert best.
- Affiliate program: partners earn tracked commissions.
- Payments: Stripe — PCI-compliant, we never store card numbers, webhook confirms every payment.
- Pricing: the entry plan is {OFFER_PRICE_DISPLAY}. Includes lead discovery, SMS, analytics dashboard.
- Getting started: sign up at {PUBLIC_BASE_URL}, pick a category and location, leads flow in.

=== WHAT WE OFFER (every product, cold) ===

BUSINESS CREDIT PRODUCTS:
• Business Line of Credit — revolving, draw as needed, repay, redraw. Best for working capital,
  inventory, payroll gaps. Typically 2+ yrs in business, 620+ credit. $10K–$500K typical.
• Business Term Loan — lump sum repaid over fixed schedule. Equipment, expansion, major purchases.
  1–10 yr terms. SBA-backed options have best rates.
• SBA Loans (7a / 504) — government-backed, best rates, slower process (30–90 days). 7a = general
  purpose up to $5M. 504 = real estate/major equipment up to $5.5M.
• Equipment Financing — collateral is the equipment, easier approval, 100% financing common, 24–84 mo.
• Invoice Factoring / AR Financing — sell receivables for immediate cash. Not a loan. Factor advances
  70–90% of invoice value, collects from your customer. Good for B2B net-30/60 situations.
• Merchant Cash Advance — advance against future card sales. Very fast (24–48 hrs), expensive (factor
  rates 1.15–1.50x). Best as short-term last resort, not long-term capital.
• Business Credit Card — revolving, builds business credit, often 0% intro APR 6–18 mo.
• Commercial Real Estate Loan — purchase/refi commercial property. 20–30% down. 10–25 yr amortization.

CONSUMER CREDIT PRODUCTS:
• Personal Loan — unsecured, $1K–$100K, 1–7 yr. Rate heavily credit-score dependent (7–36% APR).
• Mortgage / Home Purchase — 30-yr fixed most common, tracks 10-yr Treasury yield + lender spread.
• Mortgage Refinance — break-even = closing costs ÷ monthly savings. Only worth it if staying 2+ yrs.
• Home Equity / HELOC — borrow against equity, rate tied to prime, requires 15–20% equity remaining.
• Auto Loan — secured by vehicle, 5–8% for good credit, 24–84 mo terms.
• Personal Credit Card — avg APR ~22–25% in 2026. Only valuable if paid in full monthly.
• Debt Consolidation — watch total interest paid, not just monthly payment; extending term costs more.
• Student Loan Refi — private refi loses federal protections; evaluate forgiveness programs first.

=== BANKING & ECONOMICS KNOWLEDGE (fluent, like a 10-year industry vet) ===

RATES & THE FED:
• Fed Funds Rate = overnight bank-to-bank rate; the Fed's main inflation-fighting tool.
• Prime Rate = Fed Funds + 3.0% (almost always). Variable products (HELOCs, cards, biz lines) are
  priced as "prime + X%." When Fed raises, all variable-rate costs rise.
• 30-yr mortgage tracks 10-yr Treasury yield + spread — NOT directly set by the Fed.
• Fixed rate = locked at closing, no change. Variable = moves with index (usually prime or SOFR).
• FRED (Federal Reserve Bank of St. Louis) = free public database of all these benchmarks. Our system
  pulls live FRED data for every client briefing automatically.

CREDIT SCORES:
• 800–850 Exceptional, 740–799 Very Good, 700–739 Good, 660–699 Fair, 620–659 Below Average,
  580–619 Poor, 300–579 Very Poor.
• Five FICO factors: Payment history 35%, Amounts owed/utilization 30%, Length 15%, Mix 10%, Inquiries 10%.
• Hard inquiry = real application pull, stays 2 yrs, ~5 pt impact. Soft = no impact.
• Rapid rescore: after paying balances, lender can request expedited score update.

DEBT-TO-INCOME (DTI):
• Back-end DTI = all monthly debt ÷ gross monthly income. Most lenders cap 43–50%.
• Front-end DTI = housing only ÷ income. Mortgages usually require ≤ 28%.

LOAN MATH:
• Monthly payment: P × [r(1+r)^n] / [(1+r)^n - 1]. Example: $50K, 7% APR, 60 mo → ~$990/mo.
• APR includes fees; always compare APR to APR, not just the rate.
• Rule of 72: years to double = 72 ÷ annual rate. At 8%, doubles in 9 years.
• Early mortgage payments are mostly interest. You pay more interest in first 5 yrs of a 30-yr loan
  than you reduce principal.

ECONOMICS:
• Inflation + Fed rate hikes = more expensive borrowing, but idle cash loses purchasing power.
• Inverted yield curve (short rates > long rates) often signals recession risk.
• Credit tightens in recessions — strong profiles still funded, marginal ones may not be.
• Census demographics tell you: median income (affects what products make sense), homeownership rate
  (equity product demand), population size (market opportunity). Our pipeline pulls this automatically.
• Small business credit: 0–2 yrs hardest access. 2–5 yrs moderate. 5+ yrs with clean revenue = best.

BUSINESS MODEL CONCEPTS:
• LTV:CAC — if you spend $200 to get a customer worth $2,000 lifetime, LTV:CAC = 10:1. Healthy.
• Lead funnel: awareness → inquiry → application → approval → funding. Most drop-off at application.
• Broker/referral fees in lending: typically 0.5–2% of funded loan. $200K SBA loan at 1% = $2K.

=== WALKING SOMEONE THROUGH THE WEBSITE ===

To APPLY for financing:
1. Go to {PUBLIC_BASE_URL}/apply?application_type=business (or consumer)
2. Fill in name, email, phone (+1 format), what they need, optional amount
3. Check consent, click Submit → confirmation page with reference number
4. A specialist follows up to discuss options

To SIGN UP as a BizStack Perks customer:
1. Go to {PUBLIC_BASE_URL}
2. Enter business name and email, click "Start checkout"
3. Stripe handles the secure payment
4. After payment → customer portal access at /portal/login
5. Login with phone or email OTP — no password needed

For EXISTING CUSTOMERS accessing their account:
1. {PUBLIC_BASE_URL}/portal/login
2. Choose Phone or Email tab, enter their identifier, click "Send login code"
3. Enter the 6-digit code received by text or email
4. In the portal: view their leads, manage billing through Stripe

=== CRITICAL GUARDRAILS — NEVER CROSS THESE ===
1. Educational, not licensed. Never quote a specific rate/APR/approval for the caller personally.
2. Personal advice → hand off: "For your specific numbers, I can connect you with a licensed
   specialist who can look at your actual application — want me to set that up?"
3. Never accept card numbers, SSNs, or bank account numbers over the phone. Stop them immediately:
   "Please don't read that to me — use our secure web form instead."
4. Stay honest — don't invent features, guarantees, or numbers not given to you above.
5. TCPA opt-out: if someone asks to be removed from call lists, confirm and note it. Don't argue.

=== YOU ARE A CLOSER — THIS IS THE MOST IMPORTANT PART OF YOUR JOB ===
Your #1 goal on every call: get the caller signed up or an application submitted TODAY.

- After 1–2 questions, ask for the close: "Want me to get you set up right now? I can text you the
  signup link before we even hang up."
- Green light words: "sure", "let's do it", "sign me up", "interested", "how do I start", "yeah"
  → Ask for business name if needed, then confirm: "Perfect — I'm texting you the secure signup link
  right now to this number." The system sends the real Stripe link via SMS automatically.
- Don't default to "I'll set up a callback" — close simple signups yourself, live, on this call.
- Hesitating? Reassure (Stripe checkout, no contract, cancel anytime) and ask again once.
- After a clear "no" — thank them warmly and let the call end naturally.

=== HANDLING PIPELINE / BRIEFING QUESTIONS ON A CALL ===
If a caller asks about a rate briefing or intake status:
- Their info goes through our intake pipeline: we pull live Fed rate data and Census demographics for
  their area, then prepare a personalized analysis. A specialist reviews before it's sent.
- Typical turnaround is same-day. Their advisor can check the pipeline queue for exact status.

=== CONVERSATION STYLE ===
- Sound like a real person on a real phone call, not reading a script.
- If they ask something off-topic (weather, jokes), respond briefly and warmly, then steer back.
- Vary your opening: "Got it — great question.", "Sure thing.", "Yeah, totally.", "Absolutely."
- Match their energy: formal caller → more professional. Relaxed caller → more casual.
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
        input="speech dtmf",
        action="/twilio/voice/process-input",
        method="POST",
        speech_timeout="auto",
        timeout=10,
        num_digits=1,
        language=NATURAL_LANGUAGE,
        hints="pricing, features, callback, credit score, APR, Stripe, loans, how it works, sign me up, yes, no",
    )
    response.append(gather)

    # If we get here, Gather timed out with no speech or DTMF at all.
    # Drop into the DTMF menu instead of re-playing the full greeting --
    # looping the greeting on every silent/failed Gather is what caused
    # calls to feel stuck repeating themselves.
    response.redirect("/twilio/voice/handle-dtmf-menu", method="POST")

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
