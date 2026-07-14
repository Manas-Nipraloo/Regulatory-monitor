import base64
import json
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx


GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_DRAFT_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"


def load_email_credentials(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def gmail_credentials_ready(path: Path) -> bool:
    credentials = load_email_credentials(path)
    required = ["client_id", "client_secret", "refresh_token", "sender_email"]
    return all(str(credentials.get(key) or "").strip() for key in required)


def create_gmail_draft(message: EmailMessage, credentials_path: Path) -> str | None:
    credentials = load_email_credentials(credentials_path)
    if not gmail_credentials_ready(credentials_path):
        return None

    access_token = _refresh_access_token(credentials)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    payload = {"message": {"raw": raw}}
    headers = {"Authorization": f"Bearer {access_token}"}
    response = httpx.post(GMAIL_DRAFT_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json().get("id")


def _refresh_access_token(credentials: dict[str, Any]) -> str:
    response = httpx.post(
        GMAIL_TOKEN_URL,
        data={
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["access_token"]
