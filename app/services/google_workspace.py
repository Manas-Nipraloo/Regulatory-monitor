import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from google.oauth2 import credentials as oauth_credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from app.config import get_settings


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
FOLDER_ID_PATTERN = re.compile(r"/folders/([a-zA-Z0-9_-]+)")
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


@dataclass(frozen=True)
class DriveUploadResult:
    pdf_url: str
    folder_url: str


def read_sheet_rows(sheet_url: str, sheet_name: str = "") -> list[dict[str, str]]:
    service = _sheets_service()
    sheet_id = _extract_sheet_id(sheet_url)
    range_name = f"{sheet_name}!A:Z" if sheet_name else "A:Z"
    result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
    values = result.get("values", [])
    if not values:
        return []

    headers = [str(header).strip() for header in values[0]]
    rows: list[dict[str, str]] = []
    for values_row in values[1:]:
        row: dict[str, str] = {}
        for index, header in enumerate(headers):
            row[header] = str(values_row[index]).strip() if index < len(values_row) else ""
        rows.append(row)
    return rows


def upload_pdf_to_drive(local_pdf: Path, run_date_path_parts: list[str], site_remark: str) -> DriveUploadResult:
    service = _drive_service()
    parent_id = _get_drive_target_folder(service, run_date_path_parts, site_remark)
    folder = service.files().get(fileId=parent_id, fields="webViewLink").execute()
    media = MediaFileUpload(str(local_pdf), mimetype="application/pdf", resumable=False)
    uploaded = _create_or_replace_pdf(service, local_pdf.name, parent_id, media)
    return DriveUploadResult(
        pdf_url=uploaded.get("webViewLink", ""),
        folder_url=folder.get("webViewLink", ""),
    )


def upload_pdf_bytes_to_drive(
    filename: str,
    content: bytes,
    run_date_path_parts: list[str],
    site_remark: str,
) -> DriveUploadResult:
    service = _drive_service()
    parent_id = _get_drive_target_folder(service, run_date_path_parts, site_remark)
    folder = service.files().get(fileId=parent_id, fields="webViewLink").execute()
    media = MediaIoBaseUpload(BytesIO(content), mimetype="application/pdf", resumable=False)
    uploaded = _create_or_replace_pdf(service, filename, parent_id, media)
    return DriveUploadResult(
        pdf_url=uploaded.get("webViewLink", ""),
        folder_url=folder.get("webViewLink", ""),
    )


def google_workspace_enabled() -> bool:
    return _oauth_credentials_ready() or _service_account_ready()


def pdf_exists_in_drive(run_date_path_parts: list[str], site_remark: str, filename: str) -> str | None:
    """Return the webViewLink of an existing PDF in the dated Drive folder, or None.

    Does NOT create any folders — if the target folder path does not exist yet,
    the file cannot be there, so it returns None.
    """
    service = _drive_service()
    parent_id = _extract_drive_folder_id(get_settings().google_drive_root_folder_url)
    for folder_name in [*run_date_path_parts, site_remark]:
        parent_id = _find_folder(service, folder_name, parent_id)
        if not parent_id:
            return None
    file_id = _find_file(service, filename, parent_id, mime_type="application/pdf")
    if not file_id:
        return None
    info = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return info.get("webViewLink")


def _find_folder(service: Any, name: str, parent_id: str) -> str | None:
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{_escape_query(name)}' "
        f"and '{parent_id}' in parents "
        "and trashed=false"
    )
    found = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    files = found.get("files", [])
    return files[0]["id"] if files else None


def _get_drive_target_folder(service: Any, run_date_path_parts: list[str], site_remark: str) -> str:
    settings = get_settings()
    parent_id = _extract_drive_folder_id(settings.google_drive_root_folder_url)
    for folder_name in [*run_date_path_parts, site_remark]:
        parent_id = _get_or_create_folder(service, folder_name, parent_id)
    return parent_id


def _create_or_replace_pdf(service: Any, filename: str, parent_id: str, media: Any) -> dict[str, str]:
    existing_id = _find_file(service, filename, parent_id, mime_type="application/pdf")
    if existing_id:
        return service.files().update(
            fileId=existing_id,
            media_body=media,
            fields="id, webViewLink",
        ).execute()

    metadata = {"name": filename, "parents": [parent_id]}
    return service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()


def _find_file(service: Any, name: str, parent_id: str, mime_type: str | None = None) -> str | None:
    mime_filter = f"and mimeType='{mime_type}' " if mime_type else ""
    query = (
        f"name='{_escape_query(name)}' "
        f"and '{parent_id}' in parents "
        f"{mime_filter}"
        "and trashed=false"
    )
    found = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = found.get("files", [])
    return files[0]["id"] if files else None


def _drive_service() -> Any:
    return build("drive", "v3", credentials=_credentials(DRIVE_SCOPES), cache_discovery=False)


def _sheets_service() -> Any:
    return build("sheets", "v4", credentials=_credentials(SHEETS_SCOPES), cache_discovery=False)


def _credentials(scopes: list[str]) -> Any:
    oauth = _load_oauth_credentials(scopes)
    if oauth:
        return oauth
    return _load_service_account_credentials(scopes)


def _load_oauth_credentials(scopes: list[str]) -> oauth_credentials.Credentials | None:
    settings = get_settings()
    path = settings.google_oauth_credentials_file
    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not all(str(data.get(key) or "").strip() for key in ("client_id", "client_secret", "refresh_token")):
        return None

    return oauth_credentials.Credentials(
        token=None,
        refresh_token=data["refresh_token"],
        token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=scopes,
    )


def _load_service_account_credentials(scopes: list[str]) -> service_account.Credentials:
    settings = get_settings()
    if not settings.google_service_account_file:
        raise RuntimeError("Google OAuth credentials or GOOGLE_SERVICE_ACCOUNT_FILE must be configured.")
    return service_account.Credentials.from_service_account_file(
        settings.google_service_account_file,
        scopes=scopes,
    )


def _oauth_credentials_ready() -> bool:
    settings = get_settings()
    path = settings.google_oauth_credentials_file
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False
    return all(str(data.get(key) or "").strip() for key in ("client_id", "client_secret", "refresh_token"))


def _service_account_ready() -> bool:
    settings = get_settings()
    return bool(settings.google_service_account_file and Path(settings.google_service_account_file).exists())


def _get_or_create_folder(service: Any, name: str, parent_id: str) -> str:
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{_escape_query(name)}' "
        f"and '{parent_id}' in parents "
        "and trashed=false"
    )
    found = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = found.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    created = service.files().create(body=metadata, fields="id").execute()
    return created["id"]


def _extract_drive_folder_id(folder_url: str) -> str:
    match = FOLDER_ID_PATTERN.search(folder_url)
    if match:
        return match.group(1)
    if folder_url and "/" not in folder_url:
        return folder_url
    raise ValueError("Google Drive root folder URL or ID is invalid.")


def _extract_sheet_id(sheet_url: str) -> str:
    match = SHEET_ID_PATTERN.search(sheet_url)
    if match:
        return match.group(1)
    if sheet_url and "/" not in sheet_url:
        return sheet_url
    raise ValueError("Google Sheet URL or ID is invalid.")


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
