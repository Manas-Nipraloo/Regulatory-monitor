import smtplib
from copy import deepcopy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.services.gmail_draft import load_email_credentials


def smtp_credentials_ready(path: Path) -> bool:
    credentials = load_email_credentials(path)
    required = ["smtp_host", "smtp_username", "smtp_password", "sender_email"]
    return all(str(credentials.get(key) or "").strip() for key in required)


def send_smtp_email(message: EmailMessage, credentials_path: Path) -> bool:
    credentials = load_email_credentials(credentials_path)
    if not smtp_credentials_ready(credentials_path):
        return False

    recipients = _message_recipients(message, credentials)
    if not recipients:
        return False

    smtp_host = str(credentials["smtp_host"]).strip()
    smtp_port = int(credentials.get("smtp_port") or 465)
    smtp_username = str(credentials["smtp_username"]).strip()
    smtp_password = str(credentials["smtp_password"])
    use_ssl = bool(credentials.get("smtp_use_ssl", smtp_port == 465))
    use_starttls = bool(credentials.get("smtp_use_starttls", smtp_port == 587))

    send_message = deepcopy(message)
    if "Bcc" in send_message:
        del send_message["Bcc"]

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=60) as smtp:
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(send_message, from_addr=smtp_username, to_addrs=recipients)
            return True

    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as smtp:
        if use_starttls:
            smtp.starttls()
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(send_message, from_addr=smtp_username, to_addrs=recipients)
        return True


def _message_recipients(message: EmailMessage, credentials: dict[str, Any]) -> list[str]:
    recipients: list[str] = []
    for header in ["to", "cc", "bcc"]:
        recipients.extend(_recipient_values(credentials.get(header)))

    if not recipients:
        for header in ["To", "Cc", "Bcc"]:
            recipients.extend(_recipient_values(message.get(header)))

    return list(dict.fromkeys(recipients))


def _recipient_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []
