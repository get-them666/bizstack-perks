"""
Flask/FastAPI routes for Legal Document Business Writer
Integrates with the main application for web-based document management.
"""

from flask import Blueprint, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from functools import wraps
import logging
import os
from datetime import datetime
from pathlib import Path

from legal_document_writer import LegalDocumentWriter, PDFHandler, FormHandler


legal_bp = Blueprint('legal', __name__, url_prefix='/api/legal')
logger = logging.getLogger(__name__)

# Initialize document writer
doc_writer = LegalDocumentWriter({
    "library_path": "./legal_templates",
    "documents_dir": "./generated_documents"
})

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt', 'json'}
UPLOAD_FOLDER = "./uploaded_documents"
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)


def internal_error():
    """Log implementation details without returning them to API consumers."""
    logger.exception("Legal document request failed")
    return jsonify({"error": "Internal server error"}), 500


def resolve_stored_file(file_path):
    """Only allow PDF operations on files managed by this application."""
    resolved_path = Path(file_path).resolve()
    managed_directories = (Path(UPLOAD_FOLDER).resolve(), doc_writer.documents_dir)
    if not any(
        resolved_path.is_relative_to(directory) for directory in managed_directories
    ):
        raise ValueError("File must be in managed document storage")
    if not resolved_path.is_file():
        raise FileNotFoundError("File not found")
    return resolved_path


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def require_auth(f):
    """Decorator for routes requiring authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# Template Management Routes
# ============================================================================

@legal_bp.route('/templates', methods=['GET'])
def list_templates():
    """List all available legal document templates."""
    try:
        category = request.args.get('category')
        templates = doc_writer.library.list_templates(category=category)
        
        return jsonify({
            "status": "success",
            "count": len(templates),
            "templates": templates
        }), 200
    except Exception:
        return internal_error()


@legal_bp.route('/templates/categories', methods=['GET'])
def list_categories():
    """List all document categories."""
    try:
        categories = doc_writer.library.list_categories()
        return jsonify({
            "status": "success",
            "categories": categories
        }), 200
    except Exception:
        return internal_error()


@legal_bp.route('/templates/<template_id>', methods=['GET'])
def get_template(template_id):
    """Get specific template details."""
    try:
        template = doc_writer.library.get_template(template_id)
        return jsonify({
            "status": "success",
            "template": template
        }), 200
    except ValueError:
        return jsonify({"error": "Template not found"}), 404
    except Exception:
        return internal_error()


# ============================================================================
# Document Generation Routes
# ============================================================================

@legal_bp.route('/generate', methods=['POST'])
@require_auth
def generate_document():
    """Generate a new document from template with provided data."""
    try:
        data = request.get_json()
        
        if not data or 'template_id' not in data or 'form_data' not in data:
            return jsonify({"error": "Missing template_id or form_data"}), 400
        
        template_id = data['template_id']
        form_data = data['form_data']
        format_type = data.get('format', 'docx')
        filename = data.get('filename')
        
        result = doc_writer.generate_document(template_id, form_data, format_type, filename)
        
        if result['status'] == 'success':
            return jsonify({
                "status": "success",
                "template": result["template"],
                "file": result["file"],
                "format": result["format"],
                "size": result["size"],
            }), 201
        else:
            return jsonify({"error": "Unable to generate document"}), 400
    except Exception:
        return internal_error()


@legal_bp.route('/documents', methods=['GET'])
@require_auth
def list_generated_documents():
    """List all generated documents."""
    try:
        documents = doc_writer.list_generated_documents()
        return jsonify({
            "status": "success",
            "count": len(documents),
            "documents": documents
        }), 200
    except Exception:
        return internal_error()


@legal_bp.route('/download/<document_name>', methods=['GET'])
@require_auth
def download_document(document_name):
    """Download a generated document."""
    try:
        document_name = secure_filename(document_name)
        file_path = doc_writer.resolve_document_path(
            document_name, require_exists=False
        )
        
        if not file_path.is_file():
            return jsonify({"error": "Document not found"}), 404
        
        return send_file(
            str(file_path),
            as_attachment=True,
            download_name=document_name
        )
    except Exception:
        return internal_error()


@legal_bp.route('/delete/<document_name>', methods=['DELETE'])
@require_auth
def delete_document(document_name):
    """Delete a generated document."""
    try:
        document_name = secure_filename(document_name)
        file_path = doc_writer.resolve_document_path(
            document_name, require_exists=False
        )
        
        if not file_path.is_file():
            return jsonify({"error": "Document not found"}), 404
        
        file_path.unlink()
        return jsonify({"status": "success", "message": "Document deleted"}), 200
    except Exception:
        return internal_error()


# ============================================================================
# PDF Handling Routes
# ============================================================================

@legal_bp.route('/pdf/read', methods=['POST'])
@require_auth
def read_pdf():
    """Read and extract content from PDF."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not allowed"}), 400
        
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        
        if not doc_writer.pdf_handler:
            return jsonify({"error": "PDF support not available"}), 501
        
        result = doc_writer.pdf_handler.read_pdf(filepath)
        return jsonify(result), 200
    except Exception:
        return internal_error()


@legal_bp.route('/pdf/merge', methods=['POST'])
@require_auth
def merge_pdfs():
    """Merge multiple PDF files."""
    try:
        data = request.get_json()
        if not data or 'pdf_paths' not in data:
            return jsonify({"error": "Missing pdf_paths"}), 400

        if not isinstance(data['pdf_paths'], list):
            return jsonify({"error": "pdf_paths must be a list"}), 400

        pdf_paths = [str(resolve_stored_file(path)) for path in data['pdf_paths']]
        output_filename = data.get('output_name', f"merged_{datetime.now().timestamp()}.pdf")
        output_path = str(doc_writer.create_output_path(output_filename, "pdf"))
        
        if not doc_writer.pdf_handler:
            return jsonify({"error": "PDF support not available"}), 501
        
        result = doc_writer.pdf_handler.merge_pdfs(pdf_paths, output_path)
        return jsonify(result), 201
    except Exception:
        return internal_error()


@legal_bp.route('/pdf/split', methods=['POST'])
@require_auth
def split_pdf():
    """Split PDF into individual pages."""
    try:
        data = request.get_json()
        if not data or 'pdf_path' not in data:
            return jsonify({"error": "Missing pdf_path"}), 400
        
        pdf_path = str(resolve_stored_file(data['pdf_path']))
        pages = data.get('pages')
        output_dir = str(doc_writer.documents_dir / f"split_{datetime.now().timestamp()}")
        
        if not doc_writer.pdf_handler:
            return jsonify({"error": "PDF support not available"}), 501
        
        result = doc_writer.pdf_handler.split_pdf(pdf_path, output_dir, pages)
        return jsonify(result), 201
    except Exception:
        return internal_error()


@legal_bp.route('/pdf/watermark', methods=['POST'])
@require_auth
def add_watermark():
    """Add watermark to PDF."""
    try:
        data = request.get_json()
        required = ['pdf_path', 'watermark_text']
        if not data or not all(k in data for k in required):
            return jsonify({"error": f"Missing required fields: {required}"}), 400
        
        pdf_path = str(resolve_stored_file(data['pdf_path']))
        watermark_text = data['watermark_text']
        output_filename = data.get('output_name', f"watermarked_{datetime.now().timestamp()}.pdf")
        output_path = str(doc_writer.create_output_path(output_filename, "pdf"))
        
        if not doc_writer.pdf_handler:
            return jsonify({"error": "PDF support not available"}), 501
        
        result = doc_writer.pdf_handler.add_watermark(pdf_path, watermark_text, output_path)
        return jsonify(result), 201
    except Exception:
        return internal_error()


# ============================================================================
# Form Handling Routes
# ============================================================================

@legal_bp.route('/form/fields', methods=['POST'])
@require_auth
def read_form_fields():
    """Extract form fields from PDF."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not allowed"}), 400
        
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        
        if not doc_writer.form_handler:
            return jsonify({"error": "Form support not available"}), 501
        
        result = doc_writer.form_handler.read_form_fields(filepath)
        return jsonify(result), 200
    except Exception:
        return internal_error()


@legal_bp.route('/form/fill', methods=['POST'])
@require_auth
def fill_form():
    """Fill form fields with data."""
    try:
        data = request.get_json()
        required = ['pdf_path', 'form_data']
        if not data or not all(k in data for k in required):
            return jsonify({"error": f"Missing required fields: {required}"}), 400
        
        pdf_path = str(resolve_stored_file(data['pdf_path']))
        form_data = data['form_data']
        output_filename = data.get('output_name', f"filled_{datetime.now().timestamp()}.pdf")
        output_path = str(doc_writer.create_output_path(output_filename, "pdf"))
        
        if not doc_writer.form_handler:
            return jsonify({"error": "Form support not available"}), 501
        
        result = doc_writer.form_handler.fill_form(pdf_path, form_data, output_path)
        return jsonify(result), 201
    except Exception:
        return internal_error()


# ============================================================================
# Email Integration Routes
# ============================================================================

@legal_bp.route('/email/setup', methods=['POST'])
@require_auth
def setup_email():
    """Setup email configuration."""
    try:
        data = request.get_json()
        required = ['smtp_server', 'smtp_port', 'email', 'password']
        if not data or not all(k in data for k in required):
            return jsonify({"error": f"Missing required fields: {required}"}), 400
        
        doc_writer.setup_email(
            data['smtp_server'],
            data['smtp_port'],
            data['email'],
            data['password']
        )
        
        return jsonify({"status": "success", "message": "Email configured"}), 200
    except Exception:
        return internal_error()


@legal_bp.route('/email/send', methods=['POST'])
@require_auth
def email_document():
    """Send document via email."""
    try:
        data = request.get_json()
        required = ['document_path', 'recipient_email']
        if not data or not all(k in data for k in required):
            return jsonify({"error": f"Missing required fields: {required}"}), 400
        
        if not doc_writer.email_integration:
            return jsonify({"error": "Email not configured"}), 400

        document_name = secure_filename(Path(data['document_path']).name)
        if not document_name:
            return jsonify({"error": "Invalid document path"}), 400

        result = doc_writer.email_document(
            document_name,
            data['recipient_email'],
            data.get('subject', 'Legal Document'),
            data.get('message', '')
        )
        
        if result['status'] == 'success':
            return jsonify({
                "status": "success",
                "recipient": result["recipient"],
                "document": result["document"],
            }), 201
        else:
            return jsonify({"error": "Unable to send document"}), 400
    except Exception:
        return internal_error()


# ============================================================================
# SMS Integration Routes
# ============================================================================

@legal_bp.route('/sms/setup', methods=['POST'])
@require_auth
def setup_sms():
    """Setup SMS configuration."""
    try:
        data = request.get_json()
        required = ['twilio_account_sid', 'twilio_auth_token', 'from_number']
        if not data or not all(k in data for k in required):
            return jsonify({"error": f"Missing required fields: {required}"}), 400
        
        doc_writer.setup_sms(
            data['twilio_account_sid'],
            data['twilio_auth_token'],
            data['from_number']
        )
        
        return jsonify({"status": "success", "message": "SMS configured"}), 200
    except Exception:
        return internal_error()


@legal_bp.route('/sms/send', methods=['POST'])
@require_auth
def sms_document():
    """Send document via SMS."""
    try:
        data = request.get_json()
        required = ['document_path', 'recipient_number']
        if not data or not all(k in data for k in required):
            return jsonify({"error": f"Missing required fields: {required}"}), 400
        
        if not doc_writer.sms_integration:
            return jsonify({"error": "SMS not configured"}), 400

        document_name = secure_filename(Path(data['document_path']).name)
        if not document_name:
            return jsonify({"error": "Invalid document path"}), 400

        result = doc_writer.sms_document(
            document_name,
            data['recipient_number']
        )
        
        if result['status'] == 'success':
            return jsonify({
                "status": "success",
                "message_ids": result["message_ids"],
                "chunks_sent": result["chunks_sent"],
            }), 201
        else:
            return jsonify({"error": "Unable to send document"}), 400
    except Exception:
        return internal_error()


# ============================================================================
# File Upload Routes
# ============================================================================

@legal_bp.route('/upload', methods=['POST'])
@require_auth
def upload_document():
    """Upload a document file."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if not allowed_file(file.filename):
            return jsonify({"error": "File type not allowed"}), 400
        
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, f"{datetime.now().timestamp()}_{filename}")
        file.save(filepath)
        
        return jsonify({
            "status": "success",
            "filename": os.path.basename(filepath),
            "path": filepath,
            "size": os.path.getsize(filepath)
        }), 201
    except Exception:
        return internal_error()


# ============================================================================
# Health Check
# ============================================================================

@legal_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "Legal Document Writer",
        "pdf_support": bool(doc_writer.pdf_handler),
        "form_support": bool(doc_writer.form_handler),
        "email_configured": bool(doc_writer.email_integration),
        "sms_configured": bool(doc_writer.sms_integration)
    }), 200
