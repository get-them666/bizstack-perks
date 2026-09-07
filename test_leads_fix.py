import os
import serpapi  # Correct, verified client architecture

def run_lead_query():
    # Make sure Railway environment keys are loaded cleanly
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        print("❌ Error: Missing SERPAPI_API_KEY inside your configuration module.")
        return

    print("🛰️ Connecting to SerpApi engines...")
    try:
        client = serpapi.Client(api_key=api_key)
        
        # Pulling structured business data nodes securely
        results = client.search({
            "engine": "google",
            "q": "small business networking perks b2b",
            "location": "United States"
        })
        
        leads = results.get("organic_results", [])
        print(f"✅ Success! Captured {len(leads)} target lead indicators from search grids.")
        
        for idx, lead in enumerate(leads[:3], 1):
            print(f"   [{idx}] Lead Entity: {lead.get('title')} -> {lead.get('link')}")
            
    except Exception as e:
        print(f"❌ Execution dropped inside Lead engine workflow: {str(e)}")

if __name__ == "__main__":
    run_lead_query()
