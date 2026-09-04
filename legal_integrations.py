"""
Integration Services for Legal Document Writer
Connects legal document system with existing BizStack email/SMS systems.
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path
import json

logger = logging.getLogger(__name__)


class LegalEmailService:
    """Bridge between legal document system and existing email service."""
    
    def __init__(self, email_notifier=None, db_session=None):
        """
        Initialize email service.
        
        Args:
            email_notifier: Existing email_notifier module from bizstack-perks
            db_session: SQLAlchemy session for database operations
        """
        self.email_notifier = email_notifier
        self.db = db_session
    
    def send_document(self, document_path: Optional[str] = None,
                     recipient_email: str = "",
                     subject: str = "Legal Document", message: str = "",
                     cc: Optional[List[str]] = None, 
                     bcc: Optional[List[str]] = None) -> Dict:
        """
        Send legal document via email using existing email service.
        
        Args:
            document_path: Optional path to document file
            recipient_email: Recipient email address
            subject: Email subject
            message: Email message body
            cc: List of CC recipients
            bcc: List of BCC recipients
            
        Returns:
            Dictionary with status and message_id
        """
        try:
            if not recipient_email:
                raise ValueError("Recipient email is required")

            if document_path and not Path(document_path).exists():
                raise FileNotFoundError(f"Document not found: {document_path}")
            
            # Use existing email_notifier if available
            if self.email_notifier:
                send_with_attachment = getattr(
                    self.email_notifier, "send_with_attachment", None
                )
                if document_path and callable(send_with_attachment):
                    result = send_with_attachment(
                        to=recipient_email,
                        subject=subject,
                        message=message,
                        attachment_path=document_path,
                        cc=cc,
                        bcc=bcc
                    )
                else:
                    if document_path:
                        raise AttributeError(
                            "Email notifier does not support document attachments"
                        )
                    send_email = getattr(self.email_notifier, "send_email", None)
                    if not callable(send_email):
                        raise AttributeError(
                            "Email notifier does not provide a supported send method"
                        )
                    result = send_email(recipient_email, subject, message)

                if isinstance(result, dict):
                    return result

                logger.info(f"Document emailed to {recipient_email}: {result}")
                return {"status": "success" if result else "error"}
            else:
                logger.warning("Email service not initialized")
                return {"status": "error", "message": "Email service not available"}
        except Exception as e:
            logger.error(f"Error sending document via email: {str(e)}")
            return {"status": "error", "message": str(e)}
    
    def send_bulk_documents(self, recipients: List[Dict], documents: List[str],
                           subject: str = "Legal Documents") -> Dict:
        """
        Send multiple documents to multiple recipients.
        
        Args:
            recipients: List of {'email': str, 'name': str} dicts
            documents: List of document paths
            subject: Email subject
            
        Returns:
            Summary of sending results
        """
        results = []
        for recipient in recipients:
            for doc in documents:
                result = self.send_document(
                    doc,
                    recipient['email'],
                    subject=subject,
                    message=f"Dear {recipient.get('name', 'Recipient')},\n\nPlease find the attached document."
                )
                results.append({
                    "recipient": recipient['email'],
                    "document": Path(doc).name,
                    "status": result.get('status')
                })
        
        return {
            "total_sent": len(results),
            "successful": sum(1 for r in results if r['status'] == 'success'),
            "failed": sum(1 for r in results if r['status'] == 'error'),
            "results": results
        }
    
    def send_document_for_signature(self, document_path: str, recipient_email: str,
                                   recipient_name: str = "") -> Dict:
        """
        Send document for e-signature workflow.
        
        Args:
            document_path: Path to document
            recipient_email: Recipient email
            recipient_name: Recipient name
            
        Returns:
            E-signature workflow information
        """
        message = f"""
Dear {recipient_name or 'Recipient'},

Please review and sign the attached document.

Once signed, you can download it from your account.

Best regards,
BizStack Perks Team
        """
        
        return self.send_document(
            document_path,
            recipient_email,
            subject="Document Requires Your Signature",
            message=message
        )


class LegalSMSService:
    """Bridge between legal document system and existing SMS service."""
    
    def __init__(self, sms_manager=None, db_session=None):
        """
        Initialize SMS service.
        
        Args:
            sms_manager: Existing sms_manager module from bizstack-perks
            db_session: SQLAlchemy session for database operations
        """
        self.sms_manager = sms_manager
        self.db = db_session
    
    def send_document_link(self, recipient_phone: str, document_url: str,
                          message: Optional[str] = None) -> Dict:
        """
        Send document link via SMS.
        
        Args:
            recipient_phone: Phone number (E.164 format)
            document_url: URL to document
            message: Custom message prefix
            
        Returns:
            Dictionary with status and message_id
        """
        try:
            default_message = "Your legal document is ready for download"
            sms_body = message or default_message
            if document_url:
                sms_body = f"{sms_body}\n\n{document_url}"
            
            if self.sms_manager:
                is_twilio_manager = (
                    hasattr(self.sms_manager, "client")
                    and hasattr(self.sms_manager, "from_number")
                )
                if is_twilio_manager:
                    if not self.sms_manager.is_configured():
                        return {
                            "status": "error",
                            "message": "Twilio SMS service is not configured",
                        }
                    sent_message = self.sms_manager.client.messages.create(
                        body=sms_body,
                        from_=self.sms_manager.from_number,
                        to=recipient_phone,
                    )
                    result = {
                        "status": "success",
                        "message_sid": sent_message.sid,
                        "phone": recipient_phone,
                    }
                else:
                    result = self.sms_manager.send_sms(
                        to=recipient_phone,
                        message=sms_body
                    )
                    if isinstance(result, dict) and result.get("status") == "sent":
                        result = {**result, "status": "success"}
                logger.info(f"Document link sent to {recipient_phone}")
                return result
            else:
                logger.warning("SMS service not initialized")
                return {"status": "error", "message": "SMS service not available"}
        except Exception as e:
            logger.error(f"Error sending SMS: {str(e)}")
            return {"status": "error", "message": str(e)}
    
    def send_document_notification(self, recipient_phone: str, document_name: str,
                                   download_url: str, sender_name: str = "BizStack") -> Dict:
        """
        Send professional document notification via SMS.
        
        Args:
            recipient_phone: Phone number
            document_name: Name of document
            download_url: URL to download
            sender_name: Name of sender
            
        Returns:
            Send result
        """
        message = f"{sender_name}: Document '{document_name}' is ready."
        return self.send_document_link(recipient_phone, download_url, message)


class LegalInboundService:
    """Handle inbound email processing for legal documents."""
    
    def __init__(self, inbound_email_service=None, db_session=None):
        """
        Initialize inbound service.
        
        Args:
            inbound_email_service: Existing inbound_email module
            db_session: SQLAlchemy session
        """
        self.inbound_email = inbound_email_service
        self.db = db_session
    
    def process_received_documents(self, email_data: Dict) -> Dict:
        """
        Process documents received via email.
        
        Args:
            email_data: Email message data
            
        Returns:
            Processing result
        """
        try:
            attachments = email_data.get('attachments', [])
            sender = email_data.get('from')
            subject = email_data.get('subject')
            
            processed_docs = []
            for attachment in attachments:
                if attachment.get('content_type') in ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
                    processed_docs.append({
                        "filename": attachment.get('filename'),
                        "size": len(attachment.get('content', b'')),
                        "type": attachment.get('content_type')
                    })
            
            logger.info(f"Processed {len(processed_docs)} documents from {sender}")
            return {
                "status": "success",
                "sender": sender,
                "subject": subject,
                "documents_processed": len(processed_docs),
                "documents": processed_docs
            }
        except Exception as e:
            logger.error(f"Error processing inbound documents: {str(e)}")
            return {"status": "error", "message": str(e)}


class LegalNotificationService:
    """Handle notifications for legal document events."""
    
    def __init__(self, email_service: LegalEmailService = None, 
                 sms_service: LegalSMSService = None):
        """
        Initialize notification service.
        
        Args:
            email_service: LegalEmailService instance
            sms_service: LegalSMSService instance
        """
        self.email = email_service
        self.sms = sms_service
    
    def notify_document_generated(self, user_email: str, user_phone: str = None,
                                 document_name: str = "", template_name: str = "") -> Dict:
        """Notify user that document has been generated."""
        email_message = f"Your {template_name} has been generated successfully: {document_name}"
        
        result = {"email": None, "sms": None}
        
        if self.email and user_email:
            result["email"] = self.email.send_document(
                None,
                user_email,
                subject="Document Generated",
                message=email_message
            )
        
        if self.sms and user_phone:
            result["sms"] = self.sms.send_document_notification(
                user_phone,
                document_name,
                ""
            )
        
        return result
    
    def notify_document_shared(self, recipient_email: str, recipient_phone: str = None,
                              sharer_name: str = "", document_name: str = "",
                              download_url: str = "") -> Dict:
        """Notify recipient that document has been shared."""
        email_message = f"{sharer_name} has shared '{document_name}' with you."
        
        result = {"email": None, "sms": None}
        
        if self.email and recipient_email:
            result["email"] = self.email.send_document(
                None,
                recipient_email,
                subject=f"Document Shared: {document_name}",
                message=f"{email_message}\n\nDownload: {download_url}"
            )
        
        if self.sms and recipient_phone:
            result["sms"] = self.sms.send_document_link(
                recipient_phone,
                download_url,
                f"{sharer_name} shared document with you"
            )
        
        return result


class LegalDocumentAudit:
    """Audit logging for legal document operations."""
    
    def __init__(self, db_session=None):
        """Initialize audit service."""
        self.db = db_session
    
    def log_action(self, document_id: str, action: str, user_id: str,
                  details: Optional[Dict] = None, ip_address: str = "") -> Dict:
        """
        Log a document operation.
        
        Args:
            document_id: Document ID
            action: Action type (created, viewed, edited, shared, downloaded, emailed, sms_sent)
            user_id: User ID
            details: Additional details
            ip_address: IP address of actor
            
        Returns:
            Log entry
        """
        try:
            log_entry = {
                "document_id": document_id,
                "action": action,
                "user_id": user_id,
                "timestamp": datetime.utcnow().isoformat(),
                "details": details or {},
                "ip_address": ip_address
            }
            
            logger.info(f"Audit: {action} on document {document_id} by {user_id}")
            
            # Store in database if session available
            if self.db:
                # Implementation would depend on your ORM
                pass
            
            return {"status": "success", "log_entry": log_entry}
        except Exception as e:
            logger.error(f"Error logging action: {str(e)}")
            return {"status": "error", "message": str(e)}
    
    def get_document_history(self, document_id: str) -> List[Dict]:
        """Get complete audit history for a document."""
        # Implementation would query database
        return []


class LegalIntegrationManager:
    """Central manager for all legal document integrations."""
    
    def __init__(self, email_notifier=None, sms_manager=None, inbound_email=None, db_session=None):
        """
        Initialize integration manager.
        
        Args:
            email_notifier: Existing email service
            sms_manager: Existing SMS service
            inbound_email: Existing inbound email handler
            db_session: SQLAlchemy session
        """
        self.email_service = LegalEmailService(email_notifier, db_session)
        self.sms_service = LegalSMSService(sms_manager, db_session)
        self.inbound_service = LegalInboundService(inbound_email, db_session)
        self.notification_service = LegalNotificationService(self.email_service, self.sms_service)
        self.audit_service = LegalDocumentAudit(db_session)
    
    def send_document_via_all_channels(self, document_path: str, recipient_email: str,
                                      recipient_phone: str = None, 
                                      document_name: str = "") -> Dict:
        """
        Send document via all available channels (email and SMS).
        
        Args:
            document_path: Path to document
            recipient_email: Recipient email
            recipient_phone: Recipient phone (optional)
            document_name: Document name
            
        Returns:
            Results from all channels
        """
        results = {
            "email": None,
            "sms": None,
            "overall_status": "partial"
        }
        
        # Send via email
        if recipient_email:
            results["email"] = self.email_service.send_document(
                document_path,
                recipient_email,
                subject=document_name
            )
        
        # Send via SMS (link only)
        if recipient_phone:
            results["sms"] = self.sms_service.send_document_notification(
                recipient_phone,
                document_name,
                f"https://example.com/documents/{Path(document_path).name}"
            )
        
        # Determine overall status
        email_success = results["email"] and results["email"].get("status") == "success"
        sms_success = results["sms"] and results["sms"].get("status") == "success"
        
        if email_success and sms_success:
            results["overall_status"] = "success"
        elif email_success or sms_success:
            results["overall_status"] = "partial"
        else:
            results["overall_status"] = "failed"
        
        return results


if __name__ == "__main__":
    # Example usage
    email_svc = LegalEmailService()
    audit = LegalDocumentAudit()
    
    print("Legal Document Integration Services Ready")
