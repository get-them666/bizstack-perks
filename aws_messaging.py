"""AWS SES and SNS delivery for portal login codes."""

import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_SES_FROM_EMAIL = os.getenv("AWS_SES_FROM_EMAIL", "")
AWS_OTP_ENABLED = os.getenv("AWS_OTP_ENABLED", "").lower() == "true"
AWS_SNS_SENDER_ID = os.getenv("AWS_SNS_SENDER_ID", "")


def aws_otp_configured() -> bool:
    """Return whether the explicit AWS OTP provider configuration is complete."""
    return bool(
        AWS_OTP_ENABLED
        and AWS_REGION
        and AWS_ACCESS_KEY_ID
        and AWS_SECRET_ACCESS_KEY
    )


def aws_email_configured() -> bool:
    return aws_otp_configured() and bool(AWS_SES_FROM_EMAIL)


def _client(service_name: str):
    return boto3.client(
        service_name,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """Send a plain-text message through Amazon SES."""
    if not aws_email_configured():
        return False

    try:
        _client("ses").send_email(
            Source=AWS_SES_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
            },
        )
        logger.info("SES email sent to %s", to_email)
        return True
    except (BotoCoreError, ClientError) as exc:
        logger.error("SES email delivery failed for %s: %s", to_email, exc)
        return False


def send_sms(to_phone: str, body: str) -> bool:
    """Send an SMS through Amazon SNS."""
    if not aws_otp_configured():
        return False

    attributes: dict[str, dict[str, str]] = {
        "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}
    }
    if AWS_SNS_SENDER_ID:
        attributes["AWS.SNS.SMS.SenderID"] = {
            "DataType": "String",
            "StringValue": AWS_SNS_SENDER_ID,
        }

    try:
        _client("sns").publish(
            PhoneNumber=to_phone,
            Message=body,
            MessageAttributes=attributes,
        )
        logger.info("SNS SMS sent to %s", to_phone)
        return True
    except (BotoCoreError, ClientError) as exc:
        logger.error("SNS SMS delivery failed for %s: %s", to_phone, exc)
        return False
