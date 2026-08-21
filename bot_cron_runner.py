import urllib.request
import urllib.error
import time
import sys
from datetime import datetime

# Point strictly to your validated local FastAPI background bot endpoint 
TARGET_ENDPOINT = "http://127.0.0"
INTERVAL_SECONDS = 300  # Triggers the background ingestion pipeline every 5 minutes

print(f"🚀 BizStack Perks Automated Automation Cron Engine Initialized.")
print(f"🔗 Target Stream Node: {TARGET_ENDPOINT}")
print(f"⏱️ Time Loop Matrix: Executing network sync every {INTERVAL_SECONDS} seconds.")
print("-" * 80)

while True:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Create a structured POST request payload using Python's standard library
        request = urllib.request.Request(TARGET_ENDPOINT, method="POST")
        
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
