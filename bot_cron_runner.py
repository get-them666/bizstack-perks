import os
import urllib.request
import urllib.error
import time
import sys
import json
from datetime import datetime

# Setup production endpoints and fallbacks - Explicitly enforce the exact API routing path slug
TARGET_ENDPOINT = os.getenv("BIZSTACK_BOT_ENDPOINT", "https://bizstackperks.com")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "secure_bot_token_abc123")
INTERVAL_SECONDS = 300  # Sync gap loop execution pause delay sequence duration

if not BOT_API_TOKEN:
    raise RuntimeError("BOT_API_TOKEN string value must be assigned before starting the runtime worker process")

print("⚡ BizStack Perks Automated State-Webhook Cron Engine Initialized.")
print(f"🎯 Target Stream Node: {TARGET_ENDPOINT}")
print(f"⏰ Time Loop Matrix: Executing sync every {INTERVAL_SECONDS} seconds.\n" + "-"*80)

while True:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Construct the clear JSON parameter context structure mapping your FastAPI expectation structures
        payload = {"status": "APPROVED"}
        data = json.dumps(payload).encode('utf-8')
        
        request = urllib.request.Request(
            TARGET_ENDPOINT,
            data=data,
            method="POST",
            headers={
                "X-Bot-Token": BOT_API_TOKEN,
                "Content-Type": "application/json"
            }
        )
        
        with urllib.request.urlopen(request, timeout=10.0) as response:
            response_data = response.read().decode('utf-8')
            print(f"[{current_time}] ✅ Synchronized: Server handshake complete. Response: {response_data}")
            
    except urllib.error.URLError as network_error:
        print(f"[{current_time}] ❌ Interface Error: Cannot connect to ASGI server node. Details: {network_error.reason}", file=sys.stderr)
    except Exception as general_exception:
        print(f"[{current_time}] ❌ Pipeline Exception: {str(general_exception)}", file=sys.stderr)
        
    time.sleep(INTERVAL_SECONDS)
