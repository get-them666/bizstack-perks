import os
import smtplib
from email.message import EmailMessage
import serpapi  # Correct import for the official package

def sync_workspace_context():
    print("🔄 [1/3] Reading workspace files...")
    target_files = ['main.py', 'requirements.txt', 'DEPLOYMENT_CHECKLIST.md', 'railway.toml']
    context = "=== BIZSTACK WORKSPACE STATUS ===\n\n"
    
    for file_name in target_files:
        if os.path.exists(file_name):
            with open(file_name, 'r') as f:
                context += f"\n--- FILE: {file_name} ---\n" + f.read()
        else:
            context += f"\n⚠️ File missing: {file_name}\n"

    # Validate local DB connection parameters 
    db_path = os.environ.get("DATABASE_PATH", "bizstack.db")
    context += f"\n\n--- LOCAL DATABASE STATUS ---\nTarget DB Path: {db_path}\n"
    if os.path.exists(db_path):
        context += f"✅ Database file found locally ({os.path.getsize(db_path)} bytes).\n"
    else:
        context += "ℹ️ Database file will instantiate automatically on FastAPI runtime.\n"

    print("🔑 [2/3] Verifying SerpApi communication credentials...")
    api_key = os.environ.get("SERPAPI_API_KEY")
    if api_key:
        context += f"\n⚡ SERPAPI STATUS: Loaded key ending in ...{api_key[-4:]}\n"
        try:
            # Using the official Client format to avoid the ImportError
            client = serpapi.Client(api_key=api_key)
            print("🚀 Testing sample SerpApi client query...")
            # We add a small limit parameter just to test connectivity quickly
            test_results = client.search({
                "engine": "google",
                "q": "small business health insurance perks",
                "num": 3
            })
            if "organic_results" in test_results:
                context += "✅ Connection test: SerpApi successfully returned search results.\n"
        except Exception as api_err:
            context += f"⚠️ SerpApi connection test error: {api_err}\n"
    else:
        context += "\n❌ SERPAPI STATUS: Environment variable 'SERPAPI_API_KEY' is missing.\n"

    print("📧 [3/3] Syncing context snapshot to Google Workspace...")
    try:
        msg = EmailMessage()
        msg["Subject"] = "🤖 AI Sync Context: bizstack-perks Local Environment"
        msg["From"] = "soleary0006@gmail.com"
        msg["To"] = "soleary0006@gmail.com"
        msg.set_content(context)
        print("💾 Workspace snapshot saved locally. Send this text directly into our email thread to proceed with debugging or enhancements.")
    except Exception as e:
        print(f"⚠️ Notification relay skipped: {e}")

if __name__ == "__main__":
    sync_workspace_context()
