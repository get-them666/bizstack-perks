"""
Database models for Legal Document Business Writer
Integrates with SQLAlchemy for persistence.
"""

from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Boolean, ForeignKey, Table, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

# Association table for many-to-many relationship between documents and templates
document_template_association = Table(
    'document_template_association',
    Base.metadata,
    Column('document_id', String, ForeignKey('legal_document.id')),
    Column('template_id', String, ForeignKey('legal_template.id'))
)

# Association table for document sharing
document_share_association = Table(
    'document_share_association',
    Base.metadata,
    Column('document_id', String, ForeignKey('legal_document.id')),
    Column('user_id', String, ForeignKey('user.id'))
)


class LegalTemplate(Base):
    """Model for legal document templates."""
    __tablename__ = 'legal_template'
    
    id = Column(String, primary_key=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)
    description = Column(Text)
    content_template = Column(Text)
    fields = Column(Text)  # JSON string of required fields
    is_custom = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String, ForeignKey('user.id'))
    
    # Relationships
    documents = relationship("LegalDocument", secondary=document_template_association, back_populates="templates")
    creator = relationship("User", back_populates="created_templates")
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "fields": self.fields,
            "is_custom": self.is_custom,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class LegalDocument(Base):
    """Model for generated legal documents."""
    __tablename__ = 'legal_document'
    
    id = Column(String, primary_key=True)
    name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_format = Column(String(20))  # pdf, docx, txt, etc.
    file_size = Column(Integer)  # in bytes
    content_hash = Column(String(255))  # for duplicate detection
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String, ForeignKey('user.id'))
    
    # Status and tracking
    status = Column(String(50), default="draft")  # draft, final, archived, deleted
    version = Column(Integer, default=1)
    is_template = Column(Boolean, default=False)
    
    # Content
    filled_data = Column(Text)  # JSON of form data used to generate document
    metadata = Column(Text)  # JSON for additional metadata
    
    # Relationships
    templates = relationship("LegalTemplate", secondary=document_template_association, back_populates="documents")
    creator = relationship("User", back_populates="created_documents")
    audit_logs = relationship("DocumentAuditLog", back_populates="document", cascade="all, delete-orphan")
    shares = relationship("User", secondary=document_share_association)
    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "file_path": self.file_path,
            "file_format": self.file_format,
            "file_size": self.file_size,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "status": self.status,
            "version": self.version,
            "is_template": self.is_template
        }


class DocumentVersion(Base):
    """Model for document version history."""
    __tablename__ = 'document_version'
    
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey('legal_document.id'))
    version_number = Column(Integer)
    file_path = Column(String(500))
    content_hash = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String, ForeignKey('user.id'))
    change_description = Column(Text)
    
    # Relationships
    document = relationship("LegalDocument", back_populates="versions")
    creator = relationship("User")
    
    def to_dict(self):
        return {
            "id": self.id,
            "document_id": self.document_id,
            "version_number": self.version_number,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "change_description": self.change_description
        }


class DocumentAuditLog(Base):
    """Model for audit logging of document operations."""
    __tablename__ = 'document_audit_log'
    
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey('legal_document.id'))
    action = Column(String(100))  # created, viewed, edited, shared, downloaded, emailed, sms_sent, etc.
    user_id = Column(String, ForeignKey('user.id'))
    timestamp = Column(DateTime, default=datetime.utcnow)
    details = Column(Text)  # JSON for additional action details
    ip_address = Column(String(50))
    
    # Relationships
    document = relationship("LegalDocument", back_populates="audit_logs")
    user = relationship("User")
    
    def to_dict(self):
        return {
            "id": self.id,
            "document_id": self.document_id,
            "action": self.action,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "details": self.details
        }


class DocumentTransmission(Base):
    """Model for tracking email/SMS transmissions of documents."""
    __tablename__ = 'document_transmission'
    
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey('legal_document.id'))
    transmission_type = Column(String(20))  # email, sms, download
    recipient = Column(String(255))  # email address or phone number
    sent_at = Column(DateTime, default=datetime.utcnow)
    delivery_status = Column(String(50), default="pending")  # pending, sent, delivered, failed
    delivery_confirmation = Column(String(500))  # delivery receipt or error message
    sent_by = Column(String, ForeignKey('user.id'))
    message = Column(Text)  # custom message sent with document
    
    # Relationships
    document = relationship("LegalDocument")
    sender = relationship("User")
    
    def to_dict(self):
        return {
            "id": self.id,
            "document_id": self.document_id,
            "transmission_type": self.transmission_type,
            "recipient": self.recipient,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "delivery_status": self.delivery_status,
            "sent_by": self.sent_by
        }


class User(Base):
    """Extended User model for document permissions."""
    __tablename__ = 'user'
    
    id = Column(String, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20))
    name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    created_templates = relationship("LegalTemplate", back_populates="creator")
    created_documents = relationship("LegalDocument", back_populates="creator")
    
    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "phone": self.phone,
            "name": self.name
        }


class DocumentPermission(Base):
    """Model for fine-grained document permissions."""
    __tablename__ = 'document_permission'
    
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey('legal_document.id'))
    user_id = Column(String, ForeignKey('user.id'))
    permission_type = Column(String(50))  # view, edit, download, share, delete
    granted_at = Column(DateTime, default=datetime.utcnow)
    granted_by = Column(String, ForeignKey('user.id'))
    expires_at = Column(DateTime, nullable=True)  # for time-limited access
    
    def to_dict(self):
        return {
            "id": self.id,
            "document_id": self.document_id,
            "user_id": self.user_id,
            "permission_type": self.permission_type,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None
        }


class DocumentComment(Base):
    """Model for collaborative comments on documents."""
    __tablename__ = 'document_comment'
    
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey('legal_document.id'))
    user_id = Column(String, ForeignKey('user.id'))
    comment_text = Column(Text)
    line_number = Column(Integer, nullable=True)  # for commenting on specific lines
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_resolved = Column(Boolean, default=False)
    resolved_by = Column(String, ForeignKey('user.id'), nullable=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "document_id": self.document_id,
            "user_id": self.user_id,
            "comment_text": self.comment_text,
            "line_number": self.line_number,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "is_resolved": self.is_resolved
        }


class FormTemplate(Base):
    """Model for form templates used in document generation."""
    __tablename__ = 'form_template'
    
    id = Column(String, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    form_schema = Column(Text)  # JSON schema for form fields
    file_path = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String, ForeignKey('user.id'))
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by
        }
