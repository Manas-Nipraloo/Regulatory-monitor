import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from app.config import get_settings


INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_folder_name(value: str) -> str:
    cleaned = INVALID_PATH_CHARS.sub("-", value).strip(" .")
    return cleaned or "Untitled"


def dated_run_folder(run_date: date, site_remark: str | None = None) -> Path:
    settings = get_settings()
    year_folder = str(run_date.year)
    month_folder = run_date.strftime("%B %Y")
    day_folder = run_date.strftime("%d-%m-%Y")
    base = settings.data_root / year_folder / month_folder / day_folder
    if site_remark:
        base = base / safe_folder_name(site_remark)
    base.mkdir(parents=True, exist_ok=True)
    return base


def email_draft_folder(run_date: date) -> Path:
    folder = dated_run_folder(run_date) / "email-drafts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def unique_path(folder: Path, filename: str) -> Path:
    candidate = folder / safe_folder_name(filename)
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = folder / f"{stem}-{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
