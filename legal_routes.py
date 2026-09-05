"""
FastAPI routes for Legal Document Business Writer.
"""

import hmac
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from werkzeug.utils import secure_filename

from legal_document_writer import LegalDocumentWriter


legal_router = APIRouter(prefix="/api/legal", tags=["legal"])
logger = logging.getLogger(__name__)

doc_writer = LegalDocumentWriter({
    "library_path": os.getenv("LEGAL_TEMPLATES_DIR", "./legal_templates"),
    "documents_dir": os.getenv("LEGAL_DOCUMENTS_DIR", "./generated_documents"),
})

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "json"}
UPLOAD_FOLDER = Path(os.getenv("LEGAL_UPLOAD_DIR", "./uploaded_documents")).resolve()
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
LEGAL_API_TOKEN = os.getenv("LEGAL_API_TOKEN", "")
SESSION_SECRET = os.getenv("SESSION_COOKIE_SECRET", "")
MAX_UPLOAD_SIZE = int(os.getenv("LEGAL_MAX_UPLOAD_SIZE", str(50 * 1024 * 1024)))
UPLOAD_CHUNK_SIZE = 1024 * 1024


def json_response(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=jsonable_encoder(content), status_code=status_code)


def public_file_name(file_reference: str) -> str:
    """Return a managed filename without exposing its server-side path."""
    return secure_filename(str(file_reference).replace("\\", "/").rsplit("/", 1)[-1])


def public_file_result(result: dict) -> dict:
    """Replace file-system paths in document operation results with filenames."""
    public_result = result.copy()
    for field in ("file", "output"):
        if field in public_result:
            public_result[field] = public_file_name(public_result[field])
    if "output_files" in public_result:
        public_result["output_files"] = [
            public_file_name(file_reference)
            for file_reference in public_result["output_files"]
        ]
    return public_result


def internal_error() -> JSONResponse:
    """Log implementation details without returning them to API consumers."""
    logger.exception("Legal document request failed")
    return json_response({"error": "Internal server error"}, 500)


def resolve_stored_file(file_reference: str) -> Path:
    """Look up a PDF by its managed filename without constructing a client path."""
    safe_filename = secure_filename(str(file_reference).replace("\\", "/").rsplit("/", 1)[-1])
    if not safe_filename:
        raise ValueError("A valid filename is required")

    for directory in (UPLOAD_FOLDER, doc_writer.documents_dir):
        try:
            return next(
                stored_file
                for stored_file in directory.iterdir()
                if (
                    stored_file.is_file()
                    and stored_file.name == safe_filename
                    and stored_file.suffix.lower() == ".pdf"
                )
            )
        except StopIteration:
            continue

    raise FileNotFoundError("File not found")


def allowed_file(filename: Optional[str]) -> bool:
    """Check if a filename has an allowed extension."""
    return bool(filename and "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS)


def is_pdf(filename: Optional[str]) -> bool:
    return bool(filename and filename.lower().endswith(".pdf"))


def save_upload(file: UploadFile, *, pdf_only: bool = False) -> Path:
    filename = file.filename
    if not allowed_file(filename) or (pdf_only and not is_pdf(filename)):
        raise ValueError("PDF file required" if pdf_only else "File type not allowed")

    extension = filename.rsplit(".", 1)[1].lower()
    destination = UPLOAD_FOLDER / f"{uuid4().hex}.{extension}"
    bytes_written = 0
    try:
        with destination.open("wb") as output:
            while chunk := file.file.read(UPLOAD_CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_SIZE:
                    raise ValueError("File exceeds maximum upload size")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def require_auth(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Require a configured Bearer token for protected legal endpoints."""
    session_token = request.cookies.get("session_token", "")
    if SESSION_SECRET and session_token and hmac.compare_digest(session_token, SESSION_SECRET):
        return

    scheme, _, token = (authorization or "").partition(" ")
    if (
        scheme != "Bearer"
        or not token
        or not LEGAL_API_TOKEN
        or not hmac.compare_digest(token, LEGAL_API_TOKEN)
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ============================================================================
# Template Management Routes
# ============================================================================

@legal_router.get("/templates")
def list_templates(category: Optional[str] = None):
    """List all available legal document templates."""
    try:
        templates = doc_writer.library.list_templates(category=category)
        return json_response({
            "status": "success",
            "count": len(templates),
            "templates": templates,
        })
    except Exception:
        return internal_error()


@legal_router.get("/templates/categories")
def list_categories():
    """List all document categories."""
    try:
        return json_response({
            "status": "success",
            "categories": doc_writer.library.list_categories(),
        })
    except Exception:
        return internal_error()


@legal_router.get("/templates/{template_id}")
def get_template(template_id: str):
    """Get specific template details."""
    try:
        return json_response({
            "status": "success",
            "template": doc_writer.library.get_template(template_id),
        })
    except ValueError:
        return json_response({"error": "Template not found"}, 404)
    except Exception:
        return internal_error()


# ============================================================================
# Document Generation Routes
# ============================================================================

@legal_router.post("/generate", status_code=201)
def generate_document(data: dict, _: None = Depends(require_auth)):
    """Generate a new document from template with provided data."""
    try:
        if "template_id" not in data or "form_data" not in data:
            return json_response({"error": "Missing template_id or form_data"}, 400)

        result = doc_writer.generate_document(
            data["template_id"],
            data["form_data"],
            data.get("format", "docx"),
            data.get("filename"),
        )
        if result["status"] != "success":
            return json_response({"error": "Unable to generate document"}, 400)

        return json_response({
            "status": "success",
            "template": result["template"],
            "file": public_file_name(result["file"]),
            "format": result["format"],
            "size": result["size"],
        }, 201)
    except Exception:
        return internal_error()


@legal_router.get("/documents")
def list_generated_documents(_: None = Depends(require_auth)):
    """List all generated documents."""
    try:
        documents = [
            {key: value for key, value in document.items() if key != "path"}
            for document in doc_writer.list_generated_documents()
        ]
        return json_response({
            "status": "success",
            "count": len(documents),
            "documents": documents,
        })
    except Exception:
        return internal_error()


@legal_router.get("/download/{document_name}")
def download_document(document_name: str, _: None = Depends(require_auth)):
    """Download a generated document."""
    try:
        safe_name = secure_filename(document_name)
        if not safe_name:
            return json_response({"error": "Invalid document name"}, 400)
        file_path = doc_writer.resolve_document_path(safe_name, require_exists=False)
        if not file_path.is_file():
            return json_response({"error": "Document not found"}, 404)
        return FileResponse(file_path, filename=safe_name)
    except Exception:
        return internal_error()


@legal_router.delete("/delete/{document_name}")
def delete_document(document_name: str, _: None = Depends(require_auth)):
    """Delete a generated document."""
    try:
        safe_name = secure_filename(document_name)
        if not safe_name:
            return json_response({"error": "Invalid document name"}, 400)
        file_path = doc_writer.resolve_document_path(safe_name, require_exists=False)
        if not file_path.is_file():
            return json_response({"error": "Document not found"}, 404)
        file_path.unlink()
        return json_response({"status": "success", "message": "Document deleted"})
    except Exception:
        return internal_error()


# ============================================================================
# PDF Handling Routes
# ============================================================================

@legal_router.post("/pdf/read")
def read_pdf(file: UploadFile = File(...), _: None = Depends(require_auth)):
    """Read and extract content from a PDF."""
    filepath = None
    try:
        filepath = save_upload(file, pdf_only=True)
        if not doc_writer.pdf_handler:
            return json_response({"error": "PDF support not available"}, 501)
        return json_response(doc_writer.pdf_handler.read_pdf(str(filepath)))
    except ValueError as exc:
        return json_response({"error": str(exc)}, 400)
    except Exception:
        return internal_error()
    finally:
        if filepath:
            filepath.unlink(missing_ok=True)


@legal_router.post("/pdf/merge", status_code=201)
def merge_pdfs(data: dict, _: None = Depends(require_auth)):
    """Merge multiple managed PDF files."""
    try:
        if not isinstance(data.get("pdf_paths"), list):
            return json_response({"error": "pdf_paths must be a list"}, 400)
        pdf_paths = [str(resolve_stored_file(path)) for path in data["pdf_paths"]]
        output_filename = data.get("output_name", f"merged_{datetime.now().timestamp()}.pdf")
        output_path = str(doc_writer.create_output_path(output_filename, "pdf"))
        if not doc_writer.pdf_handler:
            return json_response({"error": "PDF support not available"}, 501)
        return json_response(
            public_file_result(doc_writer.pdf_handler.merge_pdfs(pdf_paths, output_path)),
            201,
        )
    except (ValueError, FileNotFoundError):
        return json_response({"error": "PDF not found"}, 404)
    except Exception:
        return internal_error()


@legal_router.post("/pdf/split", status_code=201)
def split_pdf(data: dict, _: None = Depends(require_auth)):
    """Split a managed PDF into individual pages."""
    try:
        if "pdf_path" not in data:
            return json_response({"error": "Missing pdf_path"}, 400)
        pdf_path = str(resolve_stored_file(data["pdf_path"]))
        output_dir = doc_writer.documents_dir / f"split_{uuid4().hex}"
        if not doc_writer.pdf_handler:
            return json_response({"error": "PDF support not available"}, 501)
        result = doc_writer.pdf_handler.split_pdf(
            pdf_path, str(output_dir), data.get("pages")
        )
        output_files = []
        for output_file in result.get("output_files", []):
            destination = doc_writer.documents_dir / (
                f"split_{uuid4().hex}_{public_file_name(output_file)}"
            )
            Path(output_file).replace(destination)
            output_files.append(str(destination))
        if "output_files" in result:
            result = result.copy()
            result["output_files"] = output_files
        output_dir.rmdir()
        return json_response(
            public_file_result(result),
            201,
        )
    except ValueError:
        return json_response({"error": "Invalid PDF filename"}, 400)
    except FileNotFoundError:
        return json_response({"error": "PDF not found"}, 404)
    except Exception:
        return internal_error()


@legal_router.post("/pdf/watermark", status_code=201)
def add_watermark(data: dict, _: None = Depends(require_auth)):
    """Add a watermark to a managed PDF."""
    try:
        if "pdf_path" not in data or "watermark_text" not in data:
            return json_response({"error": "Missing required fields: ['pdf_path', 'watermark_text']"}, 400)
        pdf_path = str(resolve_stored_file(data["pdf_path"]))
        output_filename = data.get("output_name", f"watermarked_{datetime.now().timestamp()}.pdf")
        output_path = str(doc_writer.create_output_path(output_filename, "pdf"))
        if not doc_writer.pdf_handler:
            return json_response({"error": "PDF support not available"}, 501)
        return json_response(
            public_file_result(
                doc_writer.pdf_handler.add_watermark(
                    pdf_path, data["watermark_text"], output_path
                )
            ),
            201,
        )
    except ValueError:
        return json_response({"error": "Invalid PDF filename"}, 400)
    except FileNotFoundError:
        return json_response({"error": "PDF not found"}, 404)
    except Exception:
        return internal_error()


# ============================================================================
# Form Handling Routes
# ============================================================================

@legal_router.post("/form/fields")
def read_form_fields(file: UploadFile = File(...), _: None = Depends(require_auth)):
    """Extract fields from a PDF form."""
    filepath = None
    try:
        filepath = save_upload(file, pdf_only=True)
        if not doc_writer.form_handler:
            return json_response({"error": "Form support not available"}, 501)
        return json_response(
            public_file_result(doc_writer.form_handler.read_form_fields(str(filepath)))
        )
    except ValueError as exc:
        return json_response({"error": str(exc)}, 400)
    except Exception:
        return internal_error()
    finally:
        if filepath:
            filepath.unlink(missing_ok=True)


@legal_router.post("/form/fill", status_code=201)
def fill_form(data: dict, _: None = Depends(require_auth)):
    """Fill fields in a managed PDF form."""
    try:
        if "pdf_path" not in data or "form_data" not in data:
            return json_response({"error": "Missing required fields: ['pdf_path', 'form_data']"}, 400)
        pdf_path = str(resolve_stored_file(data["pdf_path"]))
        output_filename = data.get("output_name", f"filled_{datetime.now().timestamp()}.pdf")
        output_path = str(doc_writer.create_output_path(output_filename, "pdf"))
        if not doc_writer.form_handler:
            return json_response({"error": "Form support not available"}, 501)
        return json_response(
            public_file_result(
                doc_writer.form_handler.fill_form(pdf_path, data["form_data"], output_path)
            ),
            201,
        )
    except ValueError:
        return json_response({"error": "Invalid PDF filename"}, 400)
    except FileNotFoundError:
        return json_response({"error": "PDF not found"}, 404)
    except Exception:
        return internal_error()


# ============================================================================
# Email and SMS Integration Routes
# ============================================================================

@legal_router.post("/email/setup")
def setup_email(data: dict, _: None = Depends(require_auth)):
    """Set up email configuration."""
    try:
        required = ["smtp_server", "smtp_port", "email", "password"]
        if not all(key in data for key in required):
            return json_response({"error": f"Missing required fields: {required}"}, 400)
        doc_writer.setup_email(
            data["smtp_server"],
            data["smtp_port"],
            data["email"],
            data["password"],
            data.get("enable_tls", True),
        )
        return json_response({"status": "success", "message": "Email configured"})
    except Exception:
        return internal_error()


@legal_router.post("/email/send", status_code=201)
def email_document(data: dict, _: None = Depends(require_auth)):
    """Send a generated document by email."""
    try:
        if "document_path" not in data or "recipient_email" not in data:
            return json_response({"error": "Missing required fields: ['document_path', 'recipient_email']"}, 400)
        if not doc_writer.email_integration:
            return json_response({"error": "Email not configured"}, 400)
        document_name = secure_filename(Path(data["document_path"]).name)
        if not document_name:
            return json_response({"error": "Invalid document path"}, 400)
        result = doc_writer.email_document(
            document_name,
            data["recipient_email"],
            data.get("subject", "Legal Document"),
            data.get("message", ""),
        )
        if result["status"] == "success":
            return json_response({
                "status": "success",
                "recipient": result["recipient"],
                "document": result["document"],
            }, 201)
        return json_response({"error": "Unable to send document"}, 400)
    except Exception:
        return internal_error()


@legal_router.post("/sms/setup")
def setup_sms(data: dict, _: None = Depends(require_auth)):
    """Set up SMS configuration."""
    try:
        required = ["twilio_account_sid", "twilio_auth_token", "from_number"]
        if not all(key in data for key in required):
            return json_response({"error": f"Missing required fields: {required}"}, 400)
        doc_writer.setup_sms(data["twilio_account_sid"], data["twilio_auth_token"], data["from_number"])
        return json_response({"status": "success", "message": "SMS configured"})
    except Exception:
        return internal_error()


@legal_router.post("/sms/send", status_code=201)
def sms_document(data: dict, _: None = Depends(require_auth)):
    """Send a generated document by SMS."""
    try:
        if "document_path" not in data or "recipient_number" not in data:
            return json_response({"error": "Missing required fields: ['document_path', 'recipient_number']"}, 400)
        if not doc_writer.sms_integration:
            return json_response({"error": "SMS not configured"}, 400)
        document_name = secure_filename(Path(data["document_path"]).name)
        if not document_name:
            return json_response({"error": "Invalid document path"}, 400)
        result = doc_writer.sms_document(document_name, data["recipient_number"])
        if result["status"] == "success":
            return json_response({
                "status": "success",
                "message_ids": result.get("message_ids", []),
                "chunks_sent": result.get("chunks_sent", 0),
            }, 201)
        return json_response(
            {"error": result.get("message", "Unable to send document")},
            400,
        )
    except Exception:
        return internal_error()


# ============================================================================
# File Upload Routes
# ============================================================================

@legal_router.post("/upload", status_code=201)
def upload_document(file: UploadFile = File(...), _: None = Depends(require_auth)):
    """Upload a document file."""
    try:
        filepath = save_upload(file)
        return json_response({
            "status": "success",
            "document_id": filepath.name,
            "size": filepath.stat().st_size,
        }, 201)
    except ValueError as exc:
        return json_response({"error": str(exc)}, 400)
    except Exception:
        return internal_error()


# ============================================================================
# Health Check
# ============================================================================

@legal_router.get("/health")
def health_check():
    """Health check endpoint."""
    return json_response({
        "status": "healthy",
        "service": "Legal Document Writer",
        "pdf_support": bool(doc_writer.pdf_handler),
        "form_support": bool(doc_writer.form_handler),
        "email_configured": bool(doc_writer.email_integration),
        "sms_configured": bool(doc_writer.sms_integration),
    })
