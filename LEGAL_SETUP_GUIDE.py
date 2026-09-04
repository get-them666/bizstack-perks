"""
Legal Document Writer - Integration Setup Guide
Complete guide for integrating the legal document system with bizstack-perks
"""

# STEP 1: Installation
# =======================

"""
1. Install dependencies:
   pip install -r legal_requirements.txt

2. Copy all legal_*.py files to your project root:
   - legal_document_writer.py    (main module)
   - legal_routes.py             (API endpoints)
   - legal_models.py             (database models)
   - legal_config.py             (configuration)
   - legal_integrations.py       (service bridges)

3. Copy HTML template:
   - templates/legal_documents.html

4. Create environment file (.env) with settings from legal_config.py and a
   LEGAL_API_TOKEN value for protected legal API endpoints.
"""


# STEP 2: Database Setup
# =======================

"""
from legal_models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Create database engine
engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///legal_documents.db'))

# Create all tables
Base.metadata.create_all(engine)

# Create session factory
Session = sessionmaker(bind=engine)
session = Session()
"""


# STEP 3: FastAPI Integration
# =======================

"""
# In your main FastAPI application (main.py):

from fastapi import FastAPI
from legal_routes import legal_router
from legal_models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

app = FastAPI()

# Register legal document router
app.include_router(legal_router)

# Setup database
engine = create_engine(os.getenv('DATABASE_URL'))
Base.metadata.create_all(engine)

# Now the following routes are available:
# GET  /api/legal/health
# GET  /api/legal/templates
# GET  /api/legal/templates/categories
# GET  /api/legal/templates/<template_id>
# POST /api/legal/generate
# GET  /api/legal/documents
# GET  /api/legal/download/<document_name>
# DELETE /api/legal/delete/<document_name>
# POST /api/legal/pdf/read
# POST /api/legal/pdf/merge
# POST /api/legal/pdf/split
# POST /api/legal/pdf/watermark
# POST /api/legal/form/fields
# POST /api/legal/form/fill
# POST /api/legal/email/setup
# POST /api/legal/email/send
# POST /api/legal/sms/setup
# POST /api/legal/sms/send
# POST /api/legal/upload
"""


# STEP 4: Email Integration
# =======================

"""
# Option A: Using existing email_notifier module

from legal_integrations import LegalEmailService
from email_notifier import send_with_attachment

legal_email = LegalEmailService(
    email_notifier=send_with_attachment,
    db_session=session
)

# Send document
result = legal_email.send_document(
    document_path="./generated_documents/my_nda.pdf",
    recipient_email="client@example.com",
    subject="NDA for Review",
    message="Please review and sign the attached NDA."
)

# Option B: Using via API

import requests

headers = {
    'Authorization': 'Bearer your-auth-token',
    'Content-Type': 'application/json'
}

# Setup email
requests.post('http://localhost:5000/api/legal/email/setup', 
    json={
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'email': 'your-email@gmail.com',
        'password': 'app-password'
    },
    headers=headers
)

# Send document
requests.post('http://localhost:5000/api/legal/email/send',
    json={
        'document_path': './generated_documents/my_nda.pdf',
        'recipient_email': 'client@example.com',
        'subject': 'NDA for Review',
        'message': 'Please review and sign.'
    },
    headers=headers
)
"""


# STEP 5: SMS Integration  
# =======================

"""
# Option A: Using existing sms_manager module

from legal_integrations import LegalSMSService
from sms_manager import send_sms

legal_sms = LegalSMSService(
    sms_manager=send_sms,
    db_session=session
)

# Send document link
result = legal_sms.send_document_link(
    recipient_phone="+1234567890",
    document_url="https://example.com/documents/my_nda.pdf"
)

# Option B: Using via API

# Setup SMS
requests.post('http://localhost:5000/api/legal/sms/setup',
    json={
        'twilio_account_sid': 'your-account-sid',
        'twilio_auth_token': 'your-auth-token',
        'from_number': '+1234567890'
    },
    headers=headers
)

# Send document
requests.post('http://localhost:5000/api/legal/sms/send',
    json={
        'document_path': './generated_documents/my_nda.pdf',
        'recipient_number': '+1234567890'
    },
    headers=headers
)
"""


# STEP 6: Web Interface
# =======================

"""
# Add this route to serve the web interface:

from flask import render_template

@app.route('/legal')
def legal_documents_page():
    return render_template('legal_documents.html')

# Access at: http://localhost:5000/legal
"""


# STEP 7: Using the Python API
# =======================

"""
from legal_document_writer import LegalDocumentWriter

# Initialize
writer = LegalDocumentWriter({
    "library_path": "./legal_templates",
    "documents_dir": "./generated_documents"
})

# 1. List templates
templates = writer.library.list_templates()
print("Available templates:")
for t in templates:
    print(f"  - {t['name']}: {t['description']}")

# 2. Generate a document
result = writer.generate_document(
    template_id="nda",
    data={
        "party_name": "Acme Corporation",
        "disclosure_period": "3 years",
        "restriction_period": "5 years",
        "jurisdiction": "California"
    },
    format="pdf",
    filename="acme_nda"
)

print(f"Generated: {result['file']}")

# 3. Read a PDF
pdf_content = writer.pdf_handler.read_pdf(result['file'])
print(f"Pages: {pdf_content['num_pages']}")

# 4. Add watermark
watermarked = writer.pdf_handler.add_watermark(
    pdf_path=result['file'],
    watermark_text="CONFIDENTIAL",
    output_path="./generated_documents/acme_nda_confidential.pdf"
)

# 5. Email the document
writer.setup_email(
    smtp_server="smtp.gmail.com",
    smtp_port=587,
    email="your-email@gmail.com",
    password="app-password"
)

email_result = writer.email_document(
    result['file'],
    "client@example.com",
    subject="Your NDA",
    message="Please review and sign."
)

# 6. List generated documents
documents = writer.list_generated_documents()
print(f"Generated {len(documents)} documents")
for doc in documents:
    print(f"  - {doc['name']} ({doc['size']} bytes)")
"""


# STEP 8: Custom Templates
# =======================

"""
from legal_models import LegalTemplate
from sqlalchemy.orm import Session

session = Session()

# Create custom template
custom_template = LegalTemplate(
    id="custom_agreement",
    name="Custom Service Agreement",
    category="Agreements",
    description="Custom agreement for specific services",
    content_template="Agreement text with {field1} and {field2}...",
    fields='["field1", "field2", "field3"]',
    is_custom=True,
    created_by="user123"
)

session.add(custom_template)
session.commit()

# Now use in document generation
result = writer.generate_document(
    "custom_agreement",
    {
        "field1": "value1",
        "field2": "value2",
        "field3": "value3"
    }
)
"""


# STEP 9: Audit Logging
# =======================

"""
from legal_integrations import LegalDocumentAudit

audit = LegalDocumentAudit(db_session=session)

# Log document access
audit.log_action(
    document_id="doc123",
    action="viewed",
    user_id="user456",
    details={"ip": "192.168.1.1"},
    ip_address="192.168.1.1"
)

# Get audit history
history = audit.get_document_history("doc123")
"""


# STEP 10: Notifications
# =======================

"""
from legal_integrations import LegalNotificationService
from legal_integrations import LegalEmailService, LegalSMSService

# Setup services
email_svc = LegalEmailService(email_notifier=send_with_attachment)
sms_svc = LegalSMSService(sms_manager=send_sms)
notifier = LegalNotificationService(email_svc, sms_svc)

# Notify on generation
notifier.notify_document_generated(
    user_email="user@example.com",
    user_phone="+1234567890",
    document_name="company_nda.pdf",
    template_name="NDA"
)

# Notify on sharing
notifier.notify_document_shared(
    recipient_email="recipient@example.com",
    sharer_name="John Doe",
    document_name="contract.pdf",
    download_url="https://example.com/download/contract.pdf"
)
"""


# STEP 11: Environment Variables
# =======================

"""
Create a .env file with:

# Email
LEGAL_SMTP_SERVER=smtp.gmail.com
LEGAL_SMTP_PORT=587
LEGAL_EMAIL_ADDRESS=your-email@gmail.com
LEGAL_EMAIL_PASSWORD=your-app-password
LEGAL_SMTP_TLS=true

# SMS
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-auth-token
TWILIO_PHONE_NUMBER=+1234567890

# Database
DATABASE_URL=postgresql://user:pass@localhost/bizstack_perks

# Features
ENABLE_PDF_SUPPORT=true
ENABLE_DOCX_SUPPORT=true
ENABLE_FORM_SUPPORT=true
ENABLE_EMAIL=true
ENABLE_SMS=true
ENABLE_ENCRYPTION=true

# Security
REQUIRE_AUTH=true
ENCRYPTION_KEY=your-encryption-key-here

# Paths
LEGAL_TEMPLATES_DIR=./legal_templates
GENERATED_DOCUMENTS_DIR=./generated_documents
"""


# STEP 12: Testing
# =======================

"""
# Test the API

import requests
import json

BASE_URL = "http://localhost:5000/api/legal"
TOKEN = "your-auth-token"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# 1. Health check
response = requests.get(f"{BASE_URL}/health")
print(response.json())

# 2. List templates
response = requests.get(f"{BASE_URL}/templates", headers=headers)
templates = response.json()['templates']
print(f"Found {len(templates)} templates")

# 3. Generate document
response = requests.post(
    f"{BASE_URL}/generate",
    headers=headers,
    json={
        "template_id": "nda",
        "format": "pdf",
        "form_data": {
            "party_name": "Test Corp",
            "disclosure_period": "3 years",
            "restriction_period": "5 years",
            "jurisdiction": "California"
        }
    }
)
print(f"Generated: {response.json()['file']}")

# 4. List documents
response = requests.get(f"{BASE_URL}/documents", headers=headers)
print(f"Generated {response.json()['count']} documents")
"""


# STEP 13: Production Deployment
# =======================

"""
Before deploying to production:

1. Security:
   - Use environment variables for all secrets
   - Enable HTTPS/TLS for all connections
   - Implement proper authentication
   - Enable audit logging
   - Use encrypted database connections

2. Performance:
   - Enable template caching
   - Use async email/SMS sending
   - Implement rate limiting
   - Add database connection pooling

3. Monitoring:
   - Setup logging to file
   - Monitor API endpoints
   - Track document generation metrics
   - Setup alerts for errors

4. Backup:
   - Regular database backups
   - Archive generated documents
   - Version control templates

5. Compliance:
   - GDPR data retention policies
   - Document encryption
   - Access audit trails
   - Secure document disposal
"""


# STEP 14: Troubleshooting
# =======================

"""
Common issues and solutions:

1. PDF operations fail
   - Install: pip install PyPDF2 reportlab
   - Check: ENABLE_PDF_SUPPORT=true

2. Email not sending
   - Verify SMTP credentials
   - Check firewall/network access
   - Gmail: Enable "Less secure app access"
   - Use app passwords, not regular password

3. SMS not working
   - Verify Twilio account active
   - Check phone number format (E.164)
   - Verify account has funds

4. Database connection error
   - Check DATABASE_URL format
   - Verify database server is running
   - Check credentials

5. Import errors
   - Install all requirements: pip install -r legal_requirements.txt
   - Check Python version (3.8+)
   - Verify file paths

6. Performance issues
   - Enable template caching
   - Use database connection pooling
   - Consider async task queue for bulk operations
"""

# ============================================================================
# END OF SETUP GUIDE
# ============================================================================
# For more information, see LEGAL_DOCUMENTS_README.md
# ============================================================================
