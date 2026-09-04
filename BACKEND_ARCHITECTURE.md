# Legal Document Writer - Backend Architecture

## System Architecture

```
FRONTEND (React/Vue/etc)
    ↓
    └─→ /api/legal/* (REST endpoints)
            ↓
        BACKEND (Flask/Django)
            ├─ legal_document_writer.py (core engine)
            ├─ legal_routes.py (API handlers)
            ├─ legal_integrations.py (services)
            ├─ legal_models.py (database)
            └─ legal_config.py (settings)
            ↓
        SERVICES
            ├─ Email (SMTP)
            ├─ SMS (Twilio)
            ├─ Storage (Local/S3/etc)
            └─ Database (PostgreSQL/MySQL/SQLite)
```

---

## Backend Only Files (What Goes in Your Backend)

### 📦 Core Backend Files

```python
# These 5 files are 100% backend - no frontend needed
1. legal_document_writer.py      # Core engine
2. legal_routes.py               # API endpoints  
3. legal_models.py               # Database models
4. legal_integrations.py         # Email/SMS integration
5. legal_config.py               # Configuration
```

### 🔌 Flask Integration

```python
# In your backend app.py:

from flask import Flask
from legal_routes import legal_bp
from legal_models import Base

app = Flask(__name__)

# Register the blueprint
app.register_blueprint(legal_bp)

# Initialize database
Base.metadata.create_all(engine)

# Now these endpoints are available:
# GET  /api/legal/health
# GET  /api/legal/templates
# POST /api/legal/generate
# POST /api/legal/email/send
# POST /api/legal/sms/send
# etc. (28 endpoints total)
```

---

## Frontend Optional Files

### Optional Web UI
```
templates/legal_documents.html  # Optional - only if you want built-in UI
```

**If you're building your own frontend:**
- Skip the HTML file
- Just use the REST API endpoints
- Your frontend calls `/api/legal/*` endpoints

**If you want the built-in UI:**
- Use the HTML file
- Serves at `/legal` endpoint
- No additional frontend work needed

---

## Directory Structure for Backend

```
your-backend/
├── legal_document_writer.py      ← Backend file
├── legal_routes.py               ← Backend file
├── legal_models.py               ← Backend file
├── legal_integrations.py         ← Backend file
├── legal_config.py               ← Backend file
├── legal_requirements.txt         ← Dependencies
├── .env                          ← Configuration
│
├── app.py                        ← Your Flask app
├── requirements.txt              ← Your dependencies
│
└── templates/
    └── legal_documents.html      ← OPTIONAL web UI
```

---

## What Your Frontend Calls

If you're building a React/Vue/Angular frontend, just call these endpoints:

### Document Generation
```javascript
const response = await fetch('/api/legal/generate', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    template_id: 'nda',
    form_data: {
      party_name: 'Acme Corp',
      disclosure_period: '3 years'
    },
    format: 'pdf'
  })
});

const result = await response.json();
console.log('Generated document:', result.file);
```

### List Templates
```javascript
const response = await fetch('/api/legal/templates', {
  headers: { 'Authorization': `Bearer ${token}` }
});

const templates = await response.json();
```

### Send Email
```javascript
const response = await fetch('/api/legal/email/send', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    document_path: './generated_documents/nda.pdf',
    recipient_email: 'client@example.com',
    subject: 'NDA for Review',
    message: 'Please review the attached NDA.'
  })
});
```

---

## Installation for Backend

```bash
# 1. Install dependencies
pip install -r legal_requirements.txt

# 2. Add files to backend directory
cp legal_*.py your-backend/
cp legal_requirements.txt your-backend/

# 3. Register in Flask
# Edit app.py (see above)

# 4. Configure environment
cp .env.example .env
# Edit .env with your settings

# 5. Initialize database
python legal_quickstart.py

# 6. Test
curl http://localhost:5000/api/legal/health
```

---

## Configuration for Backend

Create `.env` in your backend root:

```env
# Database
DATABASE_URL=postgresql://user:pass@localhost/bizstack_perks

# Email
LEGAL_SMTP_SERVER=smtp.gmail.com
LEGAL_SMTP_PORT=587
LEGAL_EMAIL_ADDRESS=your-email@gmail.com
LEGAL_EMAIL_PASSWORD=app-password

# SMS (optional)
TWILIO_ACCOUNT_SID=ACxxxxx
TWILIO_AUTH_TOKEN=xxxxx
TWILIO_PHONE_NUMBER=+1234567890

# Features
ENABLE_PDF_SUPPORT=true
ENABLE_EMAIL=true
ENABLE_SMS=false

# Security
REQUIRE_AUTH=true
```

---

## Backend + Existing Bizstack Services

### Email Integration
```python
# legal_integrations.py already wraps your email_notifier
from legal_integrations import LegalIntegrationManager
from email_notifier import send_with_attachment

manager = LegalIntegrationManager(
    email_notifier=send_with_attachment,
    sms_manager=your_sms_manager,
    inbound_email=your_inbound_email,
    db_session=session
)

# Now your backend can send documents via email
manager.email_service.send_document(doc_path, email)
```

### SMS Integration
```python
# Wraps your existing sms_manager
manager.sms_service.send_document_link(phone, doc_url)
```

---

## API Endpoints (All Backend)

All 28 endpoints are backend-only:

```
GET  /api/legal/health                        # Status check
GET  /api/legal/templates                     # List templates
GET  /api/legal/templates/<id>                # Template details
POST /api/legal/generate                      # Generate document
GET  /api/legal/documents                     # List user docs
GET  /api/legal/download/<name>               # Download document
DELETE /api/legal/delete/<name>               # Delete document
POST /api/legal/pdf/read                      # Read PDF
POST /api/legal/pdf/merge                     # Merge PDFs
POST /api/legal/pdf/split                     # Split PDF
POST /api/legal/pdf/watermark                 # Add watermark
POST /api/legal/form/fields                   # Extract fields
POST /api/legal/form/fill                     # Fill form
POST /api/legal/email/setup                   # Config email
POST /api/legal/email/send                    # Send email
POST /api/legal/sms/setup                     # Config SMS
POST /api/legal/sms/send                      # Send SMS
POST /api/legal/upload                        # Upload file
+ more...
```

**All require Bearer token auth** (except /health and /templates GET)

---

## Example: Backend Usage

Your backend code can use the writer directly:

```python
# In your backend route handlers
from legal_document_writer import LegalDocumentWriter

writer = LegalDocumentWriter()

@app.route('/generate-nda', methods=['POST'])
def generate_nda():
    data = request.get_json()
    
    result = writer.generate_document(
        template_id='nda',
        data={
            'party_name': data['party_name'],
            'disclosure_period': data['disclosure_period'],
            'jurisdiction': data['jurisdiction']
        },
        format='pdf'
    )
    
    return {'file': result['file'], 'size': result['size']}
```

Or your frontend calls the API:

```python
# Frontend calls /api/legal/generate endpoint
# Backend route handler (legal_routes.py) handles it
# Returns the generated document info
```

---

## Summary: What Goes Where

### ✅ BACKEND (Your Backend Server)
- legal_document_writer.py
- legal_routes.py
- legal_models.py
- legal_integrations.py
- legal_config.py
- legal_requirements.txt
- .env configuration
- Database (PostgreSQL/MySQL/SQLite)
- Email/SMS services

### ✅ FRONTEND (Optional - Your React/Vue/Angular app)
- API calls to `/api/legal/*` endpoints
- UI for document generation form
- Document list display
- Download buttons
- Email/SMS forms
- Settings panel

### ✅ OPTIONAL Web UI (If you don't build frontend)
- templates/legal_documents.html
- Serves at `/legal` endpoint
- Works out of the box

---

## Next Steps

1. **Copy files to backend:**
   ```bash
   cp legal_*.py /path/to/your/backend/
   cp legal_requirements.txt /path/to/your/backend/
   ```

2. **Install dependencies:**
   ```bash
   pip install -r legal_requirements.txt
   ```

3. **Register in Flask:**
   ```python
   from legal_routes import legal_bp
   app.register_blueprint(legal_bp)
   ```

4. **Configure .env**

5. **Build your frontend** to call `/api/legal/*` endpoints

---

Ready to integrate! 🚀
