from fastapi import FastAPI, Form, responses
import stripe
import os

app = FastAPI()

# Read your credentials from the Railway environment panel
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
DOMAIN = os.getenv("DOMAIN", "https://bizstackperks.com")

@app.post("/api/checkout/create")
async def create_checkout_session(email: str = Form(None), business_name: str = Form(None)):
    try:
        # Generate a standard Stripe dynamic product checkout window
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            customer_email=email,
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"BizStack Perks Entry Plan - {business_name or 'Client Portal'}",
                    },
                    'unit_amount': 4900, # Value in cents ($49.00)
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
        # If credentials are wrong, this keeps the site from breaking entirely
        return responses.RedirectResponse(url=f"{DOMAIN}/?error=Unable+to+start+checkout", status_code=303)
