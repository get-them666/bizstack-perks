"""
Legal Document Writer - Integration Setup Guide
Reflects the ACTUAL implementation in this repo: no database, no
SQLAlchemy, no Flask -- just FastAPI + filesystem storage.

This file is documentation. Nothing below is auto-run; copy snippets as
needed. Everything described here is already wired up in main.py, so for
a fresh checkout of this repo you mostly just need the environment
variables in STEP 2.
"""

# STEP 1: What's already installed
# =======================

"""
No separate install step. Legal-document support ships in the main
requirements.txt:
  - PyPDF2 / pypdf      (read/merge/split/watermark PDFs)
  - reportlab           (write PDFs from templates)
  - python-docx         (write .docx from templates)
  - pypdfform           (fillable PDF form read/fill)
  - Werkzeug            (secure_filename for upload safety)

Email uses Python's standard-library smtplib (no extra package).
SMS reuses the app's existing Twilio/SignalWire client.

Relevant files:
  - legal_document_writer.py   (template library + PDF/DOCX/form/email/SMS logic)
  - legal_routes.py            (FastAPI router, mounted at /api/legal)
  - legal_templates/           (the 10 built-in templates)
  - templates/legal_documents.html  (admin UI at /admin/legal-documents)
"""


# STEP 2: Environment variables
# =======================

"""
Required:
  LEGAL_API_TOKEN=replace-with-a-long-random-token
  LEGAL_DOCUMENTS_DIR=/app/data/legal-documents   # Railway volume path
  LEGAL_UPLOAD_DIR=/app/data/legal-uploads        # Railway volume path
  LEGAL_LINK_SECRET=replace-with-a-separate-secret

Reused from the rest of the app (nothing legal-specific to set):
  SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL, SMTP_USE_TLS
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER
  (or SIGNALWIRE_PROJECT_ID / SIGNALWIRE_API_TOKEN / SIGNALWIRE_SPACE_URL / SIGNALWIRE_PHONE_NUMBER)

Generate a token/secret with:
  python3 -c "import secrets; print(secrets.token_hex(32))"
"""


# STEP 3: FastAPI wiring (already done in main.py)
# =======================

"""
from fastapi import FastAPI
from legal_routes import legal_router

app = FastAPI()
app.include_router(legal_router)

# Routes now available:
# GET    /api/legal/health
# GET    /api/legal/templates
# GET    /api/legal/templates/categories
# GET    /api/legal/templates/{template_id}
# POST   /api/legal/generate
# GET    /api/legal/documents
# GET    /api/legal/download/{document_name}
# GET    /api/legal/share/{document_name}?expires=&token=   (no bearer token; signed link)
# DELETE /api/legal/delete/{document_name}
# POST   /api/legal/pdf/read
# POST   /api/legal/pdf/merge
# POST   /api/legal/pdf/split
# POST   /api/legal/pdf/watermark
# POST   /api/legal/form/fields
# POST   /api/legal/form/fill
# POST   /api/legal/email/setup
# POST   /api/legal/email/send
# POST   /api/legal/sms/setup
# POST   /api/legal/sms/send
# POST   /api/legal/upload
#
# Every route except /health and /share/... requires:
#   Authorization: Bearer <LEGAL_API_TOKEN>
"""


# STEP 4: Generate a document (Python API)
# =======================

"""
from legal_document_writer import LegalDocumentWriter

writer = LegalDocumentWriter({
    "library_path": "./legal_templates",
    "documents_dir": "./generated_documents",
})

# 1. List templates
for t in writer.library.list_templates():
    print(f"  - {t['name']}: {t['description']}")

# 2. Generate a document
result = writer.generate_document(
    template_id="nda",
    data={
        "party_name": "Acme Corporation",
        "disclosure_period": "3 years",
        "restriction_period": "5 years",
        "jurisdiction": "Virginia",
    },
    format="pdf",
    filename="acme_nda",
)
print(f"Generated: {result['file']}")

# 3. Read it back
pdf_content = writer.pdf_handler.read_pdf(result["file"])
print(f"Pages: {pdf_content['num_pages']}")

# 4. Watermark it
writer.pdf_handler.add_watermark(
    pdf_path=result["file"],
    watermark_text="CONFIDENTIAL",
    output_path="./generated_documents/acme_nda_confidential.pdf",
)

# 5. Email it (uses the app's SMTP_* env vars, already configured on import)
writer.email_document(
    result["file"],
    "client@example.com",
    subject="Your NDA",
    message="Please review and sign.",
)

# 6. List everything generated so far
for doc in writer.list_generated_documents():
    print(f"  - {doc['name']} ({doc['size']} bytes)")
"""


# STEP 5: Generate a document (HTTP API)
# =======================

"""
import requests

BASE_URL = "https://your-domain/api/legal"
headers = {
    "Authorization": "Bearer YOUR_LEGAL_API_TOKEN",
    "Content-Type": "application/json",
}

# Generate
response = requests.post(
    f"{BASE_URL}/generate",
    headers=headers,
    json={
        "template_id": "nda",
        "format": "pdf",
        "filename": "acme_nda",
        "form_data": {
            "party_name": "Acme Corp",
            "disclosure_period": "3 years",
            "restriction_period": "5 years",
            "jurisdiction": "Virginia",
        },
    },
)
print(response.json()["file"])

# Email it
requests.post(
    f"{BASE_URL}/email/send",
    headers=headers,
    json={
        "document_path": response.json()["file"],
        "recipient_email": "client@example.com",
        "subject": "Your NDA",
        "message": "Please review and sign the attached NDA.",
    },
)

# Text a download link instead
requests.post(
    f"{BASE_URL}/sms/send",
    headers=headers,
    json={
        "document_path": response.json()["file"],
        "recipient_number": "+15551234567",
    },
)
"""


# STEP 6: Adding a new template
# =======================

"""
Templates are plain Python dicts inside legal_document_writer.py's
LegalDocumentLibrary._initialize_templates(). No database, no migration --
add a new entry to that dict:

{
    "id": "custom_agreement",
    "name": "Custom Service Agreement",
    "category": "Agreements",
    "description": "Custom agreement for specific services",
    "content_template": "Agreement text with {field1} and {field2}...",
    "fields": ["field1", "field2", "field3"],
}

Then generate against it the same way as any built-in template:
    writer.generate_document("custom_agreement", {"field1": "...", ...})
"""


# STEP 7: Signed share links (send a client a link with no login)
# =======================

"""
Every generated document can also be shared via a time-limited,
HMAC-signed URL that does NOT require the LEGAL_API_TOKEN bearer header --
useful for texting/emailing a client a direct download link.

from legal_routes import signed_download_url

url = signed_download_url("acme_nda.pdf")
# -> https://your-domain/api/legal/share/acme_nda.pdf?expires=...&token=...

Links expire after LEGAL_LINK_TTL_SECONDS (default 86400 = 24h) and are
invalidated instantly if you rotate LEGAL_LINK_SECRET.
"""


# STEP 8: Troubleshooting
# =======================

"""
1. /api/legal/health shows pdf_support/form_support as false
   -> Check `pip show PyPDF2 reportlab python-docx pypdfform` in your venv.
      These ship in the main requirements.txt; a broken install disables
      that feature gracefully instead of crashing the app.

2. email_configured / sms_configured is false
   -> These read the app's existing SMTP_*/TWILIO_*/SIGNALWIRE_* env vars,
      not anything legal-specific. Check those.

3. 401 Unauthorized on any /api/legal/* route
   -> Confirm LEGAL_API_TOKEN is set and your request sends
      "Authorization: Bearer <exact same token>".

4. Signed share link says invalid or expired
   -> Past LEGAL_LINK_TTL_SECONDS, or LEGAL_LINK_SECRET was rotated since
      the link was generated.

5. "Document not found" on download/delete/pdf operations
   -> Filenames are sanitized and resolved strictly inside
      LEGAL_DOCUMENTS_DIR / LEGAL_UPLOAD_DIR. Use the exact filename
      returned by /generate or /upload, not an arbitrary path.
"""

# ============================================================================
# END OF SETUP GUIDE
# ============================================================================
# For the full API reference, see LEGAL_DOCUMENTS_README.md
# ============================================================================
