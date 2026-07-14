import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.google_workspace import _drive_service, _extract_drive_folder_id

settings = get_settings()
service = _drive_service()
folder_id = _extract_drive_folder_id(settings.google_drive_root_folder_url)
folder = service.files().get(fileId=folder_id, fields='id, name, webViewLink').execute()
print(folder)
