# BizStack Perks

BizStack Perks is a FastAPI app with a conversion-focused homepage, admin dashboard, Stripe Checkout payment flow, and Twilio Voice inbound/outbound call support.

## Features

- Homepage with pricing, FAQ, and live CTA wired to the backend checkout route
- Stripe Checkout session creation plus verified webhook processing
- SQLite persistence for checkout session/payment state and Twilio call events
- Twilio Voice inbound menu, status callback handling, and outbound call trigger API
- Simple admin dashboard for profile management

## Required environment variables

### Core app

```bash
export BIZSTACK_ADMIN_USER="admin"
export BIZSTACK_ADMIN_PASS="password123"
export SESSION_COOKIE_SECRET="replace-with-a-long-random-secret"
export PUBLIC_BASE_URL="https://your-domain.example"
export BOT_API_TOKEN="replace-with-a-long-random-api-token"
```

### Stripe

```bash
export STRIPE_SECRET_KEY="sk_live_or_test_..."
export STRIPE_PUBLISHABLE_KEY="pk_live_or_test_..."
export STRIPE_WEBHOOK_SECRET="whsec_..."
export PRICE_ID="price_..."
```

Optional display copy for the homepage pricing card:

```bash
export OFFER_PRICE_DISPLAY="$49 / month"
```

### Twilio Voice

```bash
export TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TWILIO_AUTH_TOKEN="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TWILIO_PHONE_NUMBER="+15550000000"
```

### Optional app storage override

```bash
export DATABASE_PATH="/absolute/path/to/bizstack.db"
```

## Local setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Export the environment variables above.

3. Start the app:

   ```bash
   uvicorn main:app --reload --port 8000
   ```

4. Open `http://127.0.0.1:8000`.

The app auto-creates the SQLite tables it needs on startup.

## Stripe Checkout flow

- Homepage CTA posts to `POST /api/checkout/create`
- Successful checkout returns to `GET /checkout/success`
- Canceled checkout returns to `GET /checkout/cancel`
- Actual payment confirmation is handled by `POST /api/stripe/webhook`

### Stripe CLI webhook testing

1. Login to Stripe CLI.
2. Forward events to the local webhook:

   ```bash
   stripe listen --forward-to http://127.0.0.1:8000/api/stripe/webhook
   ```

3. Copy the printed `whsec_...` value into `STRIPE_WEBHOOK_SECRET`.
4. Trigger a test event or complete a test checkout:

   ```bash
   stripe trigger checkout.session.completed
   ```

## Twilio Voice setup

Configure your Twilio phone number webhooks to point at your deployed HTTPS app:

- **A call comes in / Voice URL** → `POST https://your-domain.example/twilio/voice/incoming`
- **Status callback** → `POST https://your-domain.example/twilio/voice/status`

Inbound calls receive a basic greeting plus a simple menu.

### Outbound test call

Trigger an outbound call from the backend with either:

- an authenticated dashboard session cookie, or
- `X-API-Key: $BOT_API_TOKEN`

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/twilio/voice/outbound \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $BOT_API_TOKEN" \
  -d '{
    "to_number": "+15551234567",
    "message": "Hello from BizStack Perks. Your outbound voice flow is working."
  }'
```

## Deployment notes

- `PUBLIC_BASE_URL` should be the final public HTTPS origin with no trailing slash.
- Stripe and Twilio webhooks require a reachable HTTPS URL in production.
- Keep all secrets in environment variables only.
- Provision a real Stripe product/price and copy its `PRICE_ID`.
- Provision a Twilio phone number with Voice enabled before testing live calls.

## Testing

Run the focused test suite with:

```bash
python -m unittest discover -s tests -p "test_*.py"
```
