# BizStack Perks — Twilio Setup Guide

## Current Status: Trial Account

Your Twilio number is currently on a **trial account**, which plays a disclaimer: *"You are using a trial account. Recordings of this call will be stored."*

To remove this disclaimer and enable unlimited inbound/outbound calling, you need to **upgrade to a paid Twilio account**.

---

## Step 1: Upgrade Your Twilio Trial Account (2 minutes)

1. Log into [Twilio Console](https://www.twilio.com/console)
2. Click your **Account Name** → **Settings** → **Account Upgrade**
3. Add a **payment method** (credit/debit card)
4. Confirm upgrade

**Cost**: Pay-as-you-go pricing (~$0.01-0.02 per inbound call, ~$0.013 per outbound minute)

---

## Step 2: Verify Your Phone Number (5 minutes)

1. Go to **Phone Numbers** → **Manage Numbers**
2. Click your Bizstack Perks number
3. If it's not **verified**, click **Verify and Activate**
4. You'll receive a verification call or SMS—confirm it

---

## Step 3: Update Your Webhook URLs

The trial account may have different webhook settings. Ensure these are configured:

### In Twilio Console:

1. **Phone Numbers** → **Manage Numbers** → Your BizStack number
2. Scroll to **Voice & Fax**:
   - **A call comes in** → `https://your-domain.com/twilio/voice/incoming`
   - **Status callbacks** → `https://your-domain.com/twilio/voice/status`

3. Scroll to **Messaging**:
   - **A message comes in** → `https://your-domain.com/twilio/sms/inbound`

---

## Step 4: Test Your Voice Bot

Once upgraded, your inbound calls will:

1. **No longer play the trial disclaimer**
2. **Immediately connect to your voice bot**, which:
   - Greets callers with a natural greeting
   - Accepts **speech or DTMF** input (callers can speak naturally or press buttons)
   - Answers questions about pricing, features, and services
   - Offers to set up a callback

### Test the bot:

```bash
# Call your Twilio number from any phone
# Example: +1-555-0000-000

# The bot will say:
# "Welcome to BizStack Perks. We help service businesses find and convert qualified leads. 
#  What brings you in today? Are you looking to generate more leads, or do you have 
#  questions about our platform?"

# Try saying:
#   - "pricing"
#   - "features"
#   - "yes, callback"
#   - "how does it work"
```

---

## Step 5: (Optional) Integrate OpenAI for Advanced Bot

The bot has **two modes**:

1. **Fallback Mode** (enabled by default) — Rule-based responses, always works
2. **OpenAI Mode** (enhanced) — Uses GPT-4 for natural conversations

### To enable OpenAI mode:

1. Get an **OpenAI API key** from [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Set the environment variable:

```bash
export OPENAI_API_KEY="sk-proj-..."
```

3. Restart your app:

```bash
uvicorn main:app --reload
```

Now your bot will use AI to generate natural responses instead of pre-written rules.

---

## Step 6: SMS Integration (Bonus)

Once upgraded, you can also send SMS to leads:

```bash
curl -X POST http://localhost:8000/api/sms/send \
  -H "X-API-Key: $BOT_API_TOKEN" \
  -F "lead_id=1" \
  -F "message=Hi! We found a great opportunity for your business."
```

---

## Step 7: Deploy to Production

When you're ready to go live:

1. Deploy your FastAPI app to a server with HTTPS (e.g., Railway, Heroku, AWS)
2. Update `PUBLIC_BASE_URL` to your production domain
3. Update Twilio webhook URLs to your production domain
4. Test end-to-end

---

## Troubleshooting

### "Trial account" disclaimer still plays after upgrade
- **Solution**: Verify your phone number in Twilio Console. The disclaimer only appears on unverified trial numbers.

### Calls drop or don't reach the bot
- **Check**: 
  1. Phone number is active in Twilio Console
  2. Webhook URLs are correct and use HTTPS
  3. Your app is running and accessible
  4. Check app logs for errors: `docker logs <container>`

### Bot doesn't respond to speech
- **Check**: 
  1. Caller spoke clearly and waited for the prompt
  2. Twilio has speech recognition enabled (default: yes)
  3. If using OpenAI, check that `OPENAI_API_KEY` is set correctly

### "I didn't understand" responses
- This is the fallback when the bot can't parse input. **Expected behavior**.
- Caller can say clearer keywords like "pricing", "features", or "callback"

---

## Pricing Breakdown (After Upgrade)

- **Inbound calls**: $0.0085 per minute
- **Outbound calls**: $0.013 per minute
- **SMS**: $0.0075 per message (US)
- **Phone number**: $1.00 per month
- **Minimum monthly**: ~$1-5 for testing, $50+ for active lead campaigns

---

## Next Steps

1. ✅ Upgrade to paid Twilio account
2. ✅ Verify your phone number
3. ✅ Test the voice bot
4. ✅ (Optional) Enable OpenAI for smarter responses
5. ✅ Set up SMS notifications to leads
6. ✅ Deploy to production HTTPS domain

Questions? Check your app logs or test with: `docker logs -f <container_name>`
