"""
Configuration for Legal Document Business Writer
"""

import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).parent
LEGAL_TEMPLATES_DIR = BASE_DIR / "legal_templates"
GENERATED_DOCUMENTS_DIR = BASE_DIR / "generated_documents"
UPLOADED_DOCUMENTS_DIR = BASE_DIR / "uploaded_documents"

# Create directories if they don't exist
LEGAL_TEMPLATES_DIR.mkdir(exist_ok=True)
GENERATED_DOCUMENTS_DIR.mkdir(exist_ok=True)
UPLOADED_DOCUMENTS_DIR.mkdir(exist_ok=True)

# File upload settings
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt', 'json', 'xlsx', 'xls'}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
UPLOAD_FOLDER = str(UPLOADED_DOCUMENTS_DIR)

# Email settings
EMAIL_CONFIG = {
    'smtp_server': os.getenv('LEGAL_SMTP_SERVER', 'smtp.gmail.com'),
    'smtp_port': int(os.getenv('LEGAL_SMTP_PORT', 587)),
    'email': os.getenv('LEGAL_EMAIL_ADDRESS', ''),
    'password': os.getenv('LEGAL_EMAIL_PASSWORD', ''),
    'enable_tls': os.getenv('LEGAL_SMTP_TLS', 'true').lower() == 'true'
}

# SMS settings (Twilio)
SMS_CONFIG = {
    'account_sid': os.getenv('TWILIO_ACCOUNT_SID', ''),
    'auth_token': os.getenv('TWILIO_AUTH_TOKEN', ''),
    'from_number': os.getenv('TWILIO_PHONE_NUMBER', ''),
    'enabled': bool(os.getenv('TWILIO_ACCOUNT_SID', ''))
}

# Database settings
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///legal_documents.db')

# PDF settings
PDF_CONFIG = {
    'enable_compression': os.getenv('PDF_COMPRESSION', 'true').lower() == 'true',
    'default_quality': int(os.getenv('PDF_QUALITY', 100)),
    'watermark_opacity': float(os.getenv('PDF_WATERMARK_OPACITY', 0.3))
}

# Document settings
DOCUMENT_CONFIG = {
    'max_versions': int(os.getenv('MAX_DOCUMENT_VERSIONS', 10)),
    'enable_versioning': os.getenv('ENABLE_VERSIONING', 'true').lower() == 'true',
    'enable_audit_logging': os.getenv('ENABLE_AUDIT_LOG', 'true').lower() == 'true',
    'require_signature': os.getenv('REQUIRE_SIGNATURE', 'false').lower() == 'true'
}

# Signature settings
SIGNATURE_CONFIG = {
    'provider': os.getenv('SIGNATURE_PROVIDER', 'docusign'),  # docusign, adobe, hellosign
    'api_key': os.getenv('SIGNATURE_API_KEY', ''),
    'account_id': os.getenv('SIGNATURE_ACCOUNT_ID', ''),
    'enable': os.getenv('ENABLE_ESIGNATURE', 'false').lower() == 'true'
}

# Security settings
SECURITY_CONFIG = {
    'require_auth': os.getenv('REQUIRE_AUTH', 'true').lower() == 'true',
    'enable_encryption': os.getenv('ENABLE_ENCRYPTION', 'true').lower() == 'true',
    'encryption_key': os.getenv('ENCRYPTION_KEY', ''),
    'session_timeout': int(os.getenv('SESSION_TIMEOUT', 3600)),
    'max_failed_attempts': int(os.getenv('MAX_FAILED_ATTEMPTS', 5))
}

# Feature flags
FEATURES = {
    'pdf_support': os.getenv('ENABLE_PDF_SUPPORT', 'true').lower() == 'true',
    'docx_support': os.getenv('ENABLE_DOCX_SUPPORT', 'true').lower() == 'true',
    'form_support': os.getenv('ENABLE_FORM_SUPPORT', 'true').lower() == 'true',
    'email_integration': os.getenv('ENABLE_EMAIL', 'true').lower() == 'true',
    'sms_integration': os.getenv('ENABLE_SMS', 'false').lower() == 'true',
    'template_marketplace': os.getenv('ENABLE_MARKETPLACE', 'false').lower() == 'true',
    'collaborative_editing': os.getenv('ENABLE_COLLAB', 'false').lower() == 'true',
    'esignature': os.getenv('ENABLE_ESIGNATURE', 'false').lower() == 'true',
    'ocr': os.getenv('ENABLE_OCR', 'false').lower() == 'true'
}

# API settings
API_CONFIG = {
    'version': 'v1',
    'base_path': '/api/legal',
    'rate_limit': int(os.getenv('API_RATE_LIMIT', 1000)),
    'rate_limit_window': int(os.getenv('RATE_LIMIT_WINDOW', 3600))
}

# Logging
LOGGING_CONFIG = {
    'level': os.getenv('LOG_LEVEL', 'INFO'),
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': str(BASE_DIR / 'legal_documents.log'),
    'max_bytes': int(os.getenv('LOG_MAX_BYTES', 10485760)),  # 10MB
    'backup_count': int(os.getenv('LOG_BACKUP_COUNT', 5))
}

# Template configuration
TEMPLATES_CONFIG = {
    'cache_enabled': os.getenv('CACHE_TEMPLATES', 'true').lower() == 'true',
    'cache_ttl': int(os.getenv('TEMPLATE_CACHE_TTL', 3600)),
    'auto_backup': os.getenv('AUTO_BACKUP_TEMPLATES', 'true').lower() == 'true',
    'backup_frequency': os.getenv('BACKUP_FREQUENCY', 'daily')
}

# Storage settings
STORAGE_CONFIG = {
    'type': os.getenv('STORAGE_TYPE', 'local'),  # local, s3, gcs, azure
    'local_path': str(GENERATED_DOCUMENTS_DIR),
    's3_bucket': os.getenv('S3_BUCKET', ''),
    's3_region': os.getenv('S3_REGION', ''),
    'gcs_bucket': os.getenv('GCS_BUCKET', ''),
    'azure_container': os.getenv('AZURE_STORAGE_CONTAINER', '')
}

# Notification settings
NOTIFICATIONS_CONFIG = {
    'send_on_generation': os.getenv('NOTIFY_ON_GENERATION', 'false').lower() == 'true',
    'send_on_share': os.getenv('NOTIFY_ON_SHARE', 'true').lower() == 'true',
    'send_on_download': os.getenv('NOTIFY_ON_DOWNLOAD', 'false').lower() == 'true'
}

# Default document properties
DEFAULT_DOCUMENT_PROPERTIES = {
    'language': os.getenv('DEFAULT_LANGUAGE', 'en-US'),
    'timezone': os.getenv('DEFAULT_TIMEZONE', 'UTC'),
    'currency': os.getenv('DEFAULT_CURRENCY', 'USD'),
    'jurisdiction': os.getenv('DEFAULT_JURISDICTION', 'US')
}
