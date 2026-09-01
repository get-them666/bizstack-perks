"""
Outreach email generator: combines business expansion/loan-seeking signals
with local bank rate comparisons into a personalized, CAN-SPAM-compliant
outreach email.

Every email generated here MUST include a physical mailing address and an
unsubscribe mechanism per CAN-SPAM Act requirements -- this module builds
that in by default and will not produce an email without it.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from business_signals import BusinessSignal
from local_bank_rates import get_best_rates_for_region, format_rates_for_display

logger = logging.getLogger(__name__)


def generate_outreach_email(
    signal: BusinessSignal,
    sender_name: str,
    sender_company: str,
    sender_physical_address: str,
    unsubscribe_url: str,
    region: Optional[str] = None,
    loan_type: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate a personalized outreach email for a business showing a public
    expansion/loan-seeking signal, referencing real local bank rate options.

    Returns a dict with 'subject' and 'body' (plain text), both including
    required CAN-SPAM elements (physical address, unsubscribe link).
    """
    rates = get_best_rates_for_region(region=region, loan_type=loan_type, limit=3)
    formatted_rates = format_rates_for_display(rates)

    subject = _build_subject(signal)
    body = _build_body(
        signal=signal,
        formatted_rates=formatted_rates,
        sender_name=sender_name,
        sender_company=sender_company,
        sender_physical_address=sender_physical_address,
        unsubscribe_url=unsubscribe_url,
    )

    return {
        "subject": subject,
        "body": body,
        "business_name": signal.business_name,
        "signal_source": signal.source_name,
    }


def _build_subject(signal: BusinessSignal) -> str:
    """Generate a relevant, non-spammy subject line referencing the public signal."""
    if signal.signal_type == "permit":
        return f"Congrats on the expansion, {signal.business_name} — financing options to know about"
    if signal.signal_type == "news":
        return f"Saw the news about {signal.business_name} — thought this might help"
    return f"A quick note for {signal.business_name}"


def _build_body(
    signal: BusinessSignal,
    formatted_rates: List[Dict[str, str]],
    sender_name: str,
    sender_company: str,
    sender_physical_address: str,
    unsubscribe_url: str,
) -> str:
    """Build the full email body, including required CAN-SPAM elements."""
    lines: List[str] = []

    lines.append(f"Hi {signal.business_name} team,")
    lines.append("")

    # Reference the specific public signal that prompted this outreach
    if signal.signal_type == "permit":
        lines.append(
            f"I noticed a permit filing indicating {signal.business_name} is expanding "
            f"{'at ' + signal.location if signal.location else 'operations'} — congratulations on the growth!"
        )
    elif signal.signal_type == "news":
        lines.append(
            f"I came across this recent coverage: \"{signal.signal_summary}\" "
            f"({signal.source_name}) — congratulations, that's exciting news."
        )
    else:
        lines.append(f"I wanted to reach out regarding {signal.business_name}'s recent growth.")

    lines.append("")
    lines.append(
        "Expansion phases often come with financing questions, so I put together a quick "
        "snapshot of current business loan rates available locally, in case it's useful timing:"
    )
    lines.append("")

    if formatted_rates:
        for rate in formatted_rates:
            lines.append(f"• {rate['bank_name']} — {rate['loan_type']}: {rate['apr_range']} APR")
            if rate.get("loan_range") and rate["loan_range"] != "Contact bank for details":
                lines.append(f"  Loan amounts: {rate['loan_range']}")
            if rate.get("notes"):
                lines.append(f"  Note: {rate['notes']}")
        lines.append("")
        lines.append(
            "Rates shown were verified against each bank's own published rate information as of "
            f"{formatted_rates[0].get('last_verified', 'recently')} and are subject to change — "
            "worth confirming directly with the bank before applying."
        )
    else:
        lines.append(
            "I'd be glad to share current local business loan rate comparisons — just reply and "
            "I'll send over what's available in your area."
        )

    lines.append("")
    lines.append(
        f"If it would help, I'm happy to walk through options or make an introduction to a lender "
        f"that fits your situation. No pressure either way — just wanted to flag this while it might "
        f"be relevant."
    )
    lines.append("")
    lines.append(f"Best,\n{sender_name}\n{sender_company}")
    lines.append("")
    lines.append("—")
    lines.append(f"{sender_company}")
    lines.append(sender_physical_address)
    lines.append(f"Don't want emails like this? Unsubscribe here: {unsubscribe_url}")

    return "\n".join(lines)


def generate_bulk_outreach(
    signals: List[BusinessSignal],
    sender_name: str,
    sender_company: str,
    sender_physical_address: str,
    unsubscribe_url_template: str,
    region: Optional[str] = None,
    loan_type: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Generate outreach emails for a batch of signals. unsubscribe_url_template
    should contain a {business_name} placeholder if you want per-recipient
    unsubscribe tracking, e.g. "https://yoursite.com/unsubscribe?b={business_name}".
    """
    emails = []
    for signal in signals:
        unsubscribe_url = unsubscribe_url_template.format(
            business_name=signal.business_name.replace(" ", "-").lower()
        ) if "{business_name}" in unsubscribe_url_template else unsubscribe_url_template

        email = generate_outreach_email(
            signal=signal,
            sender_name=sender_name,
            sender_company=sender_company,
            sender_physical_address=sender_physical_address,
            unsubscribe_url=unsubscribe_url,
            region=region,
            loan_type=loan_type,
        )
        emails.append(email)

    return emails
