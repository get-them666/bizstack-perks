# Legal Document Business Writer Tool

A comprehensive legal document management system with PDF/form handling, email/SMS integration, and full business workflow support for the BizStack Perks application.

## Features

### 📋 Document Management
- **10+ Pre-built Legal Templates**
  - NDAs (Non-Disclosure Agreements)
  - Service Agreements
  - Employment Contracts
  - Independent Contractor Agreements
  - Business Proposals
  - Privacy Policies
  - Terms of Service
  - Purchase Agreements
  - Lease Agreements
  - Professional Invoices

- **Custom Template Support**
  - Create and save custom templates
  - Reusable form fields
  - Category organization
  - Template versioning

### 📄 PDF Handling
- **PDF Operations**
  - Read and extract text from PDFs
  - Create new PDFs from templates
  - Merge multiple PDFs
  - Split PDFs into individual pages
  - Add watermarks
  - PDF compression and optimization

### 📝 Form Management
- **Form Processing**
  - Extract form fields from PDF forms
  - Auto-fill form fields with data
  - Create form templates
  - Form field validation
  - Multi-page form support

### 📧 Email Integration
- **Email Features**
  - Send documents via email with SMTP
  - Attach multiple documents
  - Custom email messages
  - Delivery tracking
  - Email parsing for received documents

### 📱 SMS Integration
- **SMS Capabilities**
  - Send document links via SMS (Twilio)
  - Base64-encoded document transmission
  - Message templates
  - Recipient tracking
  - SMS log and history

### 🔐 Security & Compliance
- **Document Security**
  - User authentication and authorization
  - Fine-grained permissions (view, edit, download, share)
  - Audit logging for all operations
  - Document encryption support
  - Watermark protection

- **Compliance Features**
  - Version control and history
  - Audit trails
  - Signature tracking
  - GDPR-compliant data handling
  - Secure document disposal

### 🤝 Collaboration
- **Team Features**
  - Document sharing with users
  - Collaborative comments
  - Version tracking
  - Change history
  - User role management

## Installation

### Prerequisites
- Python 3.8+
- Flask/FastAPI application
- SQLAlchemy ORM
- PostgreSQL or SQLite database

### Setup

1. **Install Dependencies**
   ```bash
   pip install -r legal_requirements.txt
   ```

2. **Configure Environment Variables**
   Create a `.env` file in your project root:
   ```bash
   # Email Configuration
   LEGAL_SMTP_SERVER=smtp.gmail.com
   LEGAL_SMTP_PORT=587
   LEGAL_EMAIL_ADDRESS=your-email@gmail.com
   LEGAL_EMAIL_PASSWORD=your-app-password
   LEGAL_SMTP_TLS=true

   # SMS Configuration (Twilio)
   TWILIO_ACCOUNT_SID=your-account-sid
   TWILIO_AUTH_TOKEN=your-auth-token
   TWILIO_PHONE_NUMBER=+1234567890

   # Database
   DATABASE_URL=postgresql://user:password@localhost/bizstack_perks

   # Security
   REQUIRE_AUTH=true
   ENABLE_ENCRYPTION=true
   ENCRYPTION_KEY=your-encryption-key

   # Features
   ENABLE_PDF_SUPPORT=true
   ENABLE_DOCX_SUPPORT=true
   ENABLE_FORM_SUPPORT=true
   ENABLE_EMAIL=true
   ENABLE_SMS=false
   ENABLE_ESIGNATURE=false
   ```

3. **Initialize Database**
   ```python
   from legal_models import Base
   from sqlalchemy import create_engine
   
   engine = create_engine('DATABASE_URL')
   Base.metadata.create_all(engine)
   ```

4. **Register Routes in Main Application**
   ```python
   # In your main.py or app.py
   from legal_routes import legal_bp
   
   app.register_blueprint(legal_bp)
   ```

## API Endpoints

### Templates

#### List Templates
```bash
GET /api/legal/templates
GET /api/legal/templates?category=Agreements
```

#### Get Template Details
```bash
GET /api/legal/templates/{template_id}
```

#### List Categories
```bash
GET /api/legal/templates/categories
```

### Document Generation

#### Generate Document
```bash
POST /api/legal/generate
Content-Type: application/json
Authorization: Bearer {token}

{
  "template_id": "nda",
  "format": "docx",
  "filename": "my-nda",
  "form_data": {
    "party_name": "Acme Corp",
    "disclosure_period": "3 years",
    "restriction_period": "5 years",
    "jurisdiction": "California"
  }
}
```

#### List Generated Documents
```bash
GET /api/legal/documents
Authorization: Bearer {token}
```

#### Download Document
```bash
GET /api/legal/download/{document_name}
Authorization: Bearer {token}
```

#### Delete Document
```bash
DELETE /api/legal/delete/{document_name}
Authorization: Bearer {token}
```

### PDF Operations

#### Read PDF
```bash
POST /api/legal/pdf/read
Content-Type: multipart/form-data
Authorization: Bearer {token}

file: <pdf_file>
```

#### Merge PDFs
```bash
POST /api/legal/pdf/merge
Content-Type: application/json
Authorization: Bearer {token}

{
  "pdf_paths": ["/path/to/doc1.pdf", "/path/to/doc2.pdf"],
  "output_name": "merged_document.pdf"
}
```

#### Split PDF
```bash
POST /api/legal/pdf/split
Content-Type: application/json
Authorization: Bearer {token}

{
  "pdf_path": "/path/to/document.pdf",
  "pages": [1, 3, 5],
  "output_name": "pages"
}
```

#### Add Watermark
```bash
POST /api/legal/pdf/watermark
Content-Type: application/json
Authorization: Bearer {token}

{
  "pdf_path": "/path/to/document.pdf",
  "watermark_text": "CONFIDENTIAL",
  "output_name": "watermarked.pdf"
}
```

### Form Operations

#### Read Form Fields
```bash
POST /api/legal/form/fields
Content-Type: multipart/form-data
Authorization: Bearer {token}

file: <pdf_form>
```

#### Fill Form
```bash
POST /api/legal/form/fill
Content-Type: application/json
Authorization: Bearer {token}

{
  "pdf_path": "/path/to/form.pdf",
  "form_data": {
    "field_name": "value",
    "another_field": "another_value"
  }
}
```

### Email Integration

#### Setup Email
```bash
POST /api/legal/email/setup
Content-Type: application/json
Authorization: Bearer {token}

{
  "smtp_server": "smtp.gmail.com",
  "smtp_port": 587,
  "email": "your-email@gmail.com",
  "password": "your-app-password"
}
```

#### Send Document via Email
```bash
POST /api/legal/email/send
Content-Type: application/json
Authorization: Bearer {token}

{
  "document_path": "/path/to/document.pdf",
  "recipient_email": "recipient@example.com",
  "subject": "Your NDA",
  "message": "Please review and sign the attached NDA."
}
```

### SMS Integration

#### Setup SMS
```bash
POST /api/legal/sms/setup
Content-Type: application/json
Authorization: Bearer {token}

{
  "twilio_account_sid": "your-account-sid",
  "twilio_auth_token": "your-auth-token",
  "from_number": "+1234567890"
}
```

#### Send Document via SMS
```bash
POST /api/legal/sms/send
Content-Type: application/json
Authorization: Bearer {token}

{
  "document_path": "/path/to/document.pdf",
  "recipient_number": "+1234567890"
}
```

### File Upload

#### Upload Document
```bash
POST /api/legal/upload
Content-Type: multipart/form-data
Authorization: Bearer {token}

file: <document_file>
```

### Health Check

#### Service Status
```bash
GET /api/legal/health
```

Response:
```json
{
  "status": "healthy",
  "service": "Legal Document Writer",
  "pdf_support": true,
  "form_support": true,
  "email_configured": true,
  "sms_configured": false
}
```

## Usage Examples

### Python Integration

```python
from legal_document_writer import LegalDocumentWriter

# Initialize
writer = LegalDocumentWriter({
    "library_path": "./legal_templates",
    "documents_dir": "./generated_documents"
})

# Setup email
writer.setup_email(
    smtp_server="smtp.gmail.com",
    smtp_port=587,
    email="your-email@gmail.com",
    password="app-password"
)

# Generate document
result = writer.generate_document(
    "nda",
    {
        "party_name": "Acme Corp",
        "disclosure_period": "3 years",
        "restriction_period": "5 years",
        "jurisdiction": "California"
    },
    format="pdf"
)

# Email the document
if result['status'] == 'success':
    email_result = writer.email_document(
        result['file'],
        "recipient@example.com",
        subject="NDA for Review",
        message="Please review and sign."
    )
```

### List Available Templates

```python
# List all templates
templates = writer.library.list_templates()
for t in templates:
    print(f"{t['name']} ({t['category']})")

# List specific category
contracts = writer.library.list_templates(category="Agreements")
```

## Database Models

- **LegalTemplate**: Document templates with fields and metadata
- **LegalDocument**: Generated documents with versioning
- **DocumentVersion**: Version history tracking
- **DocumentAuditLog**: Comprehensive audit trail
- **DocumentTransmission**: Email/SMS delivery tracking
- **FormTemplate**: Form field specifications
- **DocumentPermission**: Fine-grained access control
- **DocumentComment**: Collaborative comments

## File Structure

```
legal_document_writer.py      # Main module
legal_routes.py               # Flask/FastAPI routes
legal_models.py               # SQLAlchemy models
legal_config.py               # Configuration
legal_requirements.txt         # Dependencies
legal_templates/              # Template library
generated_documents/          # Output documents
uploaded_documents/           # User uploads
```

## Configuration

All configuration is managed through environment variables (see `legal_config.py`).

Key settings:
- **Email**: SMTP server, credentials
- **SMS**: Twilio account credentials
- **Database**: SQLAlchemy database URL
- **Security**: Encryption, authentication
- **Features**: Enable/disable specific features
- **Storage**: Local, S3, GCS, or Azure storage

## Security Considerations

1. **API Authentication**: All endpoints require Bearer token
2. **Encryption**: Enable encryption for sensitive documents
3. **Audit Logging**: All operations are logged
4. **Permissions**: Implement role-based access control
5. **Data Protection**: GDPR-compliant data handling
6. **Secure Transmission**: TLS/SSL for email and SMS
7. **File Security**: Secure temporary file handling

## Performance Optimization

- Template caching for faster generation
- Batch email/SMS operations
- PDF compression options
- Asynchronous document processing (planned)
- Database query optimization

## Troubleshooting

### PDF Support Issues
```bash
pip install PyPDF2 reportlab
```

### DOCX Support Issues
```bash
pip install python-docx
```

### Form Support Issues
```bash
pip install pypdfform
```

### Email Connection Issues
- Verify SMTP credentials
- Check firewall/network access
- Ensure TLS/SSL settings match provider
- Enable "Less secure app access" for Gmail

### SMS Issues
- Verify Twilio account credentials
- Check account balance
- Verify phone number format
- Check service region restrictions

## Support & Documentation

For additional help:
1. Check `legal_config.py` for all configuration options
2. Review API endpoint examples above
3. Check application logs (`legal_documents.log`)
4. Verify database connection
5. Test with health check endpoint

## License

Proprietary - BizStack Perks Application
