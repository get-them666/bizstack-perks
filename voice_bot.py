"""
Advanced Twilio voice bot with OpenAI integration for live conversation.
Handles inbound calls with real Q&A, not just menu trees.
"""

import os
import logging
from typing import Optional
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import httpx

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


class VoiceBotResponseGenerator:
    """Generate contextual voice bot responses using OpenAI or fallback rules."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or OPENAI_API_KEY
        self.has_openai = bool(self.api_key)

    async def generate_response(self, user_input: str, context: Optional[dict] = None) -> str:
        """
        Generate a voice bot response to user input.
        Falls back to rule-based responses if OpenAI is unavailable.
        """
        if self.has_openai:
            return await self._openai_response(user_input, context)
        else:
            return self._fallback_response(user_input, context)

    async def _openai_response(self, user_input: str, context: Optional[dict] = None) -> str:
        """Generate response using OpenAI GPT-4."""
        try:
            system_prompt = """You are a friendly BizStack Perks sales assistant. You help businesses:
- Understand our lead generation and monetization platform
- Learn about pricing ($49/month entry plan)
- Get answers to common questions
- Schedule callbacks or collect contact info

Keep responses SHORT (under 30 seconds of speech), friendly, and actionable.
Ask clarifying questions if needed. Always offer a callback or website visit."""

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json={
                        "model": "gpt-4-turbo",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_input},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 150,
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
        """Rule-based responses when OpenAI is unavailable."""
        user_input = user_input.lower().strip()

        # Pricing
        if any(word in user_input for word in ["price", "cost", "how much", "fee", "monthly"]):
            return (
                "Our entry plan starts at just $49 per month. You get access to our lead discovery tools, "
                "SMS notifications, and analytics dashboard. Want to try it? Visit bizstack-perks.com or say yes for a callback."
            )

        # Features
        if any(word in user_input for word in ["what", "features", "do", "included", "get"]):
            return (
                "BizStack Perks finds hot leads in your area, sends automated SMS follow-ups, and tracks conversions. "
                "You can discover businesses from Google Maps, target high-opportunity neighborhoods, and manage everything from your dashboard. "
                "Ready to learn more?"
            )

        # How it works
        if any(word in user_input for word in ["how", "works", "process", "start"]):
            return (
                "Sign up, choose your service category and location, and we'll discover and send you leads. "
                "We handle the discovery and outreach; you close the sales. Simple as that."
            )

        # Callback
        if any(word in user_input for word in ["yes", "sure", "callback", "call", "back"]):
            return "I'll set up a callback for you with one of our specialists. They'll call within 24 hours. Thank you!"

        # Default
        return (
            "That's a great question! For more details, visit bizstack-perks.com or stay on the line for a callback. "
            "What works best for you?"
        )


def create_voice_greeting() -> str:
    """Create an initial greeting TwiML."""
    response = VoiceResponse()
    response.say(
        "Welcome to BizStack Perks. We help service businesses find and convert qualified leads. "
        "What brings you in today? Are you looking to generate more leads, or do you have questions about our platform?",
        voice="alice",
        language="en-US",
    )

    gather = Gather(
        input="speech",
        action="/twilio/voice/process-input",
        method="POST",
        speech_timeout="auto",
        timeout=10,
        language="en-US",
        hints="pricing, features, callback, yes, no, help",
    )
    response.append(gather)

    # Fallback if no input
    response.say("Sorry, I didn't catch that. Please try again.", voice="alice")
    response.redirect("/twilio/voice/incoming", method="POST")

    return str(response)


def create_callback_confirmation(phone: str, name: Optional[str] = None) -> str:
    """Create confirmation TwiML for callback setup."""
    response = VoiceResponse()
    response.say(
        f"Perfect! We have you down for a callback. A specialist will reach out within 24 hours. "
        f"Thanks for choosing BizStack Perks. Goodbye!",
        voice="alice",
    )
    response.hangup()
    return str(response)


def create_information_response(topic: str) -> str:
    """Create an informational response with follow-up prompt."""
    responses = {
        "pricing": (
            "Our entry plan is $49 per month. It includes lead discovery, SMS notifications, "
            "conversion tracking, and our analytics dashboard. No setup fees."
        ),
        "features": (
            "You get access to our lead discovery engine, which searches Google Local Services, "
            "plus SMS automation for follow-ups, real-time lead alerts, and a full analytics dashboard. "
            "Everything you need to scale your business."
        ),
        "free_trial": (
            "We offer a 14-day free trial with no credit card required. "
            "Sign up at bizstack-perks.com-slash-trial to get started right now."
        ),
    }

    message = responses.get(topic, "I'm not sure about that. Would you like a callback with more details?")

    response = VoiceResponse()
    response.say(message, voice="alice", language="en-US")
    response.say("Would you like to proceed, or do you have any other questions?", voice="alice")

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
    """Create fallback menu with DTMF options."""
    response = VoiceResponse()
    response.say(
        "I'm having trouble understanding you. Let me give you some options.",
        voice="alice",
    )

    gather = Gather(
        input="dtmf",
        action="/twilio/voice/handle-dtmf",
        method="POST",
        timeout=5,
        num_digits=1,
    )
    gather.say(
        "Press 1 to hear about pricing. "
        "Press 2 to learn about features. "
        "Press 3 to request a callback. "
        "Press 4 to visit our website.",
        voice="alice",
    )
    response.append(gather)

    response.say("Goodbye.", voice="alice")
    response.hangup()

    return str(response)
