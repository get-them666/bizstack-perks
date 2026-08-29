import stripe
import os
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# 1. Ensure your real Stripe Test Secret Key is here
stripe.api_key = "sk_test_YOUR_SECRET_KEY_HERE"

# Mount the templates directory
app.mount("/templates", StaticFiles(directory="templates"), name="templates")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("templates/index.html", "r") as f:
        return HTMLResponse(content=f.read())

# Added this route to match your exact HTML form submission endpoint
@app.post("/api/checkout/create")
async def create_checkout_session(request: Request):
    try:
        base_url = str(request.base_url).rstrip('/')
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': 'price_YOUR_PRICE_ID_HERE', # 2. Ensure your real Stripe Price ID is here
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{base_url}/templates/checkout_success.html",
            cancel_url=f"{base_url}/templates/checkout_cancel.html",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except Exception as e:
        print(f"🔴 Stripe Error: {e}")
        return RedirectResponse(url="/?error=Unable+to+start+checkout", status_code=303)

if __name__ == '__main__':
    import uvicorn
    print("🚀 Starting Updated FastAPI Checkout Patch Server...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
