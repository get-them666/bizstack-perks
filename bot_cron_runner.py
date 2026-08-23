import os
import urllib.request
import urllib.error
import time
import sys
from datetime import datetime

# Override in production with
# BIZSTACK_BOT_ENDPOINT=https://bizstackperks.com/api/bot/scrape.
TARGET_ENDPOINT = os.getenv("BIZSTACK_BOT_ENDPOINT", "http://127.0.0.1:8000/api/bot/scrape")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN")
INTERVAL_SECONDS = 300  # Triggers the background ingestion pipeline every 5 minutes

if not BOT_API_TOKEN:
    raise RuntimeError("BOT_API_TOKEN must be set before starting the cron bot")

print(f"🚀 BizStack Perks Automated Automation Cron Engine Initialized.")
print(f"🔗 Target Stream Node: {TARGET_ENDPOINT}")
print(f"⏱️ Time Loop Matrix: Executing network sync every {INTERVAL_SECONDS} seconds.")
print("-" * 80)

while True:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Create a structured POST request payload using Python's standard library
        request = urllib.request.Request(
            TARGET_ENDPOINT,
            data=b"",
            method="POST",
            headers={"X-Bizstack-Bot-Token": BOT_API_TOKEN},
        )
        
        # Open the network socket tunnel to target endpoint handler
        with urllib.request.urlopen(request, timeout=10.0) as response:
            response_data = response.read().decode('utf-8')
            print(f"[{current_time}] ✅ Synchronized: Server handshake complete. Response: {response_data}")
            
    except urllib.error.URLError as network_error:
        print(f"[{current_time}] ❌ Interface Error: Cannot connect to ASGI server node. Details: {network_error.reason}", file=sys.stderr)
    except Exception as general_exception:
        print(f"[{current_time}] ❌ Pipeline Exception: {str(general_exception)}", file=sys.stderr)
        
    # Rest the worker process thread until the next execution slot hits
    time.sleep(INTERVAL_SECONDS)
