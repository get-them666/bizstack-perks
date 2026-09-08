# Legal Document Business Writer Tool

A self-contained legal document generator with PDF/DOCX/TXT output, PDF
utilities (merge/split/watermark/read), fillable-form support, and
email/SMS delivery — mounted into the main BizStack Perks FastAPI app at
`/api/legal/*`.

This tool does **not** use a database or SQLAlchemy. Generated documents
are files on disk (`generated_documents/`, or `LEGAL_DOCUMENTS_DIR` on
Railway's persistent volume); "listing documents" means listing files in
that directory, not querying a document table.

## Features

### 📋 Document Generation
- 10 built-in templates (see `legal_templates/`): NDA, Service Agreement,
  Employment Contract, Independent Contractor Agreement, Business
  Proposal, Privacy Policy, Terms of Service, Purchase Agreement, Lease
  Agreement, Professional Invoice.
- Output as `.docx`, `.pdf`, or `.txt`.
- Templates are defined in `legal_document_writer.py`'s
  `LegalDocumentLibrary` class — add a new one there (no database needed).

### 📄 PDF Utilities
- Read/extract text from a PDF (`PyPDF2`/`pypdf`)
- Merge multiple managed PDFs
- Split a PDF into individual pages
- Add a text watermark

### 📝 Fillable Forms
- Read field names from a PDF form
- Fill a managed PDF form's fields (`pypdfform`)

### 📧 Email Delivery
- Send a generated document as an email attachment via SMTP
  (`smtplib`, standard library — no extra email service required)
- Auto-configured at startup from the app's existing `SMTP_*` env vars

### 📱 SMS Delivery
- Send a document download link via SMS, using the app's existing
  Twilio/SignalWire client (`sms_manager.py` credentials)

### 🔐 Access Control
- Every mutating/read endpoint requires
  `Authorization: Bearer $LEGAL_API_TOKEN` (see `require_auth` in
  `legal_routes.py`)
- Download links can also be shared as time-limited, HMAC-signed URLs
  (`LEGAL_LINK_SECRET`, `/api/legal/share/{document_name}`) that don't
  require the bearer token — useful for sending a client a direct link.
- Filenames are sanitized (`werkzeug.secure_filename`) and resolved
  strictly inside the managed documents directory to prevent path
  traversal.

## Installation

Already installed — this ships as part of the main app's
`requirements.txt` (`PyPDF2`, `reportlab`, `python-docx`, `pypdfform`,
`Werkzeug`). No separate install step or `legal_requirements.txt` needed.

### Required environment variables

```bash
LEGAL_API_TOKEN=replace-with-a-long-random-token   # required, bearer auth
LEGAL_DOCUMENTS_DIR=/app/data/legal-documents       # Railway volume path
LEGAL_UPLOAD_DIR=/app/data/legal-uploads            # Railway volume path
LEGAL_LINK_SECRET=replace-with-a-separate-secret    # for signed share links
```

Email and SMS reuse the app's existing `SMTP_*` / `TWILIO_*` /
`SIGNALWIRE_*` variables (see `.env.example`) — nothing legal-specific to
configure there.

### Wiring (already done in `main.py`)

```python
from legal_routes import legal_router
app.include_router(legal_router)
```

## API Endpoints

All endpoints are prefixed `/api/legal`. Endpoints marked 🔒 require
`Authorization: Bearer $LEGAL_API_TOKEN`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/templates` | List templates (optional `?category=`) |
| GET | `/templates/categories` | List template categories |
| GET | `/templates/{template_id}` | Get one template's fields |
| POST 🔒 | `/generate` | Generate a document from a template |
| GET 🔒 | `/documents` | List generated documents |
| GET 🔒 | `/download/{document_name}` | Download a generated document |
| GET | `/share/{document_name}?expires=&token=` | Time-limited signed download (no bearer token) |
| DELETE 🔒 | `/delete/{document_name}` | Delete a generated document |
| POST 🔒 | `/pdf/read` | Extract text from an uploaded PDF |
| POST 🔒 | `/pdf/merge` | Merge managed PDFs |
| POST 🔒 | `/pdf/split` | Split a managed PDF into pages |
| POST 🔒 | `/pdf/watermark` | Add a watermark to a managed PDF |
| POST 🔒 | `/form/fields` | Read fields from an uploaded PDF form |
| POST 🔒 | `/form/fill` | Fill a managed PDF form |
| POST 🔒 | `/email/setup` | Runtime override of SMTP settings |
| POST 🔒 | `/email/send` | Email a generated document |
| POST 🔒 | `/sms/setup` | Runtime override of SMS settings |
| POST 🔒 | `/sms/send` | Text a document download link |
| POST 🔒 | `/upload` | Upload a document into managed storage |
| GET | `/health` | Service status (no auth) |

### Example: generate a document

```bash
curl -X POST https://your-domain/api/legal/generate \
  -H "Authorization: Bearer $LEGAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "nda",
    "format": "pdf",
    "filename": "acme-nda",
    "form_data": {
      "party_name": "Acme Corp",
      "disclosure_period": "3 years",
      "restriction_period": "5 years",
      "jurisdiction": "Virginia"
    }
  }'
```

### Example: health check (no auth)

```bash
curl https://your-domain/api/legal/health
```

```json
{
  "status": "healthy",
  "service": "Legal Document Writer",
  "pdf_support": true,
  "form_support": true,
  "email_configured": true,
  "sms_configured": true
}
```

## File Structure

```
legal_document_writer.py   # Template library, PDF/form handlers, LegalDocumentWriter
legal_routes.py            # FastAPI router mounted at /api/legal
legal_templates/           # Built-in template definitions
generated_documents/       # Output documents (or LEGAL_DOCUMENTS_DIR on Railway)
uploaded_documents/        # User uploads (or LEGAL_UPLOAD_DIR on Railway)
templates/legal_documents.html  # Admin UI at /admin/legal-documents
```

## Security Notes

- Bearer token auth (`LEGAL_API_TOKEN`) gates every document
  read/write/delete/email/SMS endpoint.
- Signed share links expire (`LEGAL_LINK_TTL_SECONDS`, default 24h) and
  are HMAC-verified against `LEGAL_LINK_SECRET` — rotate that secret to
  invalidate all outstanding share links at once.
- All file paths are sanitized and confined to the managed documents/
  uploads directories — no arbitrary filesystem access.
- No document database means no document-level audit log or granular
  per-user permissions today. If you need that, track it in the main
  SQLite database rather than reintroducing a separate ORM.

## Troubleshooting

**PDF/DOCX/form support shows `false` in `/api/legal/health`**
Check `pip show PyPDF2 reportlab python-docx pypdfform` — these are in
the main `requirements.txt`; a broken install would disable that feature
gracefully rather than crash.

**Email/SMS shows not configured**
These read the app's existing `SMTP_*` / `TWILIO_*` / `SIGNALWIRE_*`
variables — check those, not anything legal-specific.

**401 Unauthorized**
Confirm `LEGAL_API_TOKEN` is set and the request sends
`Authorization: Bearer <token>` with the exact same value.

**Signed share link says invalid/expired**
Links expire after `LEGAL_LINK_TTL_SECONDS` (default 24h) or if
`LEGAL_LINK_SECRET` was rotated since the link was created.

## License

Proprietary — BizStack Perks Application
