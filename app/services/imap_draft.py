import imaplib
import time
from email.message import EmailMessage
from pathlib import Path

from app.services.gmail_draft import load_email_credentials


def imap_credentials_ready(path: Path) -> bool:
    credentials = load_email_credentials(path)
    required = ["smtp_username", "smtp_password", "sender_email"]
    return all(str(credentials.get(key) or "").strip() for key in required)


def save_imap_draft(message: EmailMessage, credentials_path: Path) -> bool:
    credentials = load_email_credentials(credentials_path)
    if not imap_credentials_ready(credentials_path):
        return False

    imap_host = str(credentials.get("imap_host") or "imap.hostinger.com").strip()
    imap_port = int(credentials.get("imap_port") or 993)
    username = str(credentials["smtp_username"]).strip()
    password = str(credentials["smtp_password"])
    preferred_folder = str(credentials.get("imap_drafts_folder") or "Drafts").strip()

    folder_errors: list[str] = []
    with imaplib.IMAP4_SSL(imap_host, imap_port) as imap:
        imap.login(username, password)
        for folder in _draft_folder_candidates(imap, preferred_folder):
            ok, error = _append_draft(imap, folder, message)
            if ok:
                return True
            if error:
                folder_errors.append(f"{folder}: {error}")

    detail = "; ".join(folder_errors) if folder_errors else "no Drafts folder accepted APPEND"
    raise RuntimeError(f"IMAP draft save failed ({detail}).")


def _draft_folder_candidates(imap: imaplib.IMAP4_SSL, preferred_folder: str) -> list[str]:
    folders = [preferred_folder, "Drafts", "INBOX.Drafts"]
    status, data = imap.list()
    if status == "OK":
        for item in data:
            if not item:
                continue
            folder = item.decode("utf-8", errors="ignore").rsplit(" ", 1)[-1].strip('"')
            if "draft" in folder.casefold():
                folders.append(folder)
    return list(dict.fromkeys(folder for folder in folders if folder))


def _append_draft(imap: imaplib.IMAP4_SSL, folder: str, message: EmailMessage) -> tuple[bool, str | None]:
    status, data = imap.append(
        folder,
        "\\Draft",
        imaplib.Time2Internaldate(time.time()),
        message.as_bytes(),
    )
    if status == "OK":
        return True, None

    detail = ""
    if data:
        detail = " ".join(
            item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item)
            for item in data
            if item
        ).strip()
    return False, detail or status
