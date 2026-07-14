import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import get_settings
from app.schemas import ArticleResult, DailyRunResponse, RunHistoryArticle, RunHistoryEntry


HISTORY_TABLE = "run_history"


def history_file() -> Path:
    path = get_settings().data_root / "run_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def list_history(limit: int = 50, run_date: date | None = None) -> list[RunHistoryEntry]:
    limit = max(1, min(limit, 500))
    # Prefer the shared Supabase table so every computer sees the same history.
    if _supabase_enabled():
        try:
            return _list_supabase(limit, run_date)
        except Exception:
            pass  # fall back to the local backup below
    return _list_local(limit, run_date)


def record_history(
    response: DailyRunResponse,
    sources: list[str],
    upload_drive: bool,
    save_webmail_draft: bool,
    send_email: bool,
) -> RunHistoryEntry:
    entry = _history_entry(
        run_date=response.run_date,
        sources=sources,
        articles=response.articles,
        upload_drive=upload_drive,
        save_webmail_draft=save_webmail_draft,
        send_email=send_email,
        draft_path=response.draft_path,
        webmail_draft_saved=response.webmail_draft_saved,
        webmail_draft_error=response.webmail_draft_error,
        email_sent=response.email_sent,
        email_error=response.email_error,
        status=_status_from_response(response),
        message=response.message,
    )
    _store_entry(entry)
    return entry


def record_manual_history(
    run_date: date,
    sources: list[str],
    articles: list[ArticleResult],
    upload_drive: bool,
    save_webmail_draft: bool,
    send_email: bool,
    status: str,
    message: str,
    draft_path: Path | None = None,
    webmail_draft_saved: bool = False,
    webmail_draft_error: str | None = None,
    email_sent: bool = False,
    email_error: str | None = None,
) -> RunHistoryEntry:
    entry = _history_entry(
        run_date=run_date,
        sources=sources,
        articles=articles,
        upload_drive=upload_drive,
        save_webmail_draft=save_webmail_draft,
        send_email=send_email,
        draft_path=draft_path,
        webmail_draft_saved=webmail_draft_saved,
        webmail_draft_error=webmail_draft_error,
        email_sent=email_sent,
        email_error=email_error,
        status=status,
        message=message,
    )
    _store_entry(entry)
    return entry


def _history_entry(
    run_date: date,
    sources: list[str],
    articles: list[ArticleResult],
    upload_drive: bool,
    save_webmail_draft: bool,
    send_email: bool,
    draft_path: Path | None,
    webmail_draft_saved: bool,
    webmail_draft_error: str | None,
    email_sent: bool,
    email_error: str | None,
    status: str,
    message: str,
) -> RunHistoryEntry:
    return RunHistoryEntry(
        id=uuid4().hex,
        created_at=datetime.now(UTC),
        run_date=run_date,
        sources=sources,
        articles_found=len(articles),
        upload_drive=upload_drive,
        save_webmail_draft=save_webmail_draft,
        send_email=send_email,
        draft_path=draft_path,
        webmail_draft_saved=webmail_draft_saved,
        webmail_draft_error=webmail_draft_error,
        email_sent=email_sent,
        email_error=email_error,
        articles=[
            RunHistoryArticle(
                site_remark=article.site_remark,
                title=article.title,
                source_url=article.source_url,
                published_date=article.published_date,
                drive_pdf_url=article.drive_pdf_url,
                drive_folder_url=article.drive_folder_url,
                summary=article.summary,
            )
            for article in articles
        ],
        status=status,
        message=message,
    )


def _store_entry(entry: RunHistoryEntry) -> None:
    # Always keep a local backup copy on this machine.
    try:
        _append_local(entry)
    except Exception:
        pass

    # Push the shared copy to Supabase when it is configured.
    if _supabase_enabled():
        try:
            _insert_supabase(entry)
        except Exception:
            # Never let a network hiccup break a run; the local backup still saved.
            pass


def _status_from_response(response: DailyRunResponse) -> str:
    if response.email_error or response.webmail_draft_error or "Issues:" in response.message:
        return "issues"
    if response.articles_found:
        return "completed"
    return "empty"


# --------------------------------------------------------------------------- #
# Supabase (shared store, via the PostgREST HTTP API — no DB driver needed)
# --------------------------------------------------------------------------- #
def _supabase_enabled() -> bool:
    settings = get_settings()
    return bool(settings.supabase_url and settings.supabase_key)


def _supabase_endpoint() -> str:
    base = get_settings().supabase_url.rstrip("/")
    return f"{base}/rest/v1/{HISTORY_TABLE}"


def _supabase_headers() -> dict[str, str]:
    key = get_settings().supabase_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _insert_supabase(entry: RunHistoryEntry) -> None:
    payload = {
        "id": entry.id,
        "created_at": entry.created_at.isoformat(),
        "run_date": entry.run_date.isoformat(),
        "articles_found": entry.articles_found,
        "status": entry.status,
        "data": entry.model_dump(mode="json"),
    }
    with httpx.Client(timeout=20, trust_env=False) as client:
        response = client.post(
            _supabase_endpoint(),
            headers={**_supabase_headers(), "Prefer": "return=minimal"},
            content=json.dumps(payload),
        )
        response.raise_for_status()


def _list_supabase(limit: int, run_date: date | None) -> list[RunHistoryEntry]:
    params = {"select": "data", "order": "created_at.desc", "limit": str(limit)}
    if run_date:
        params["run_date"] = f"eq.{run_date.isoformat()}"
    with httpx.Client(timeout=20, trust_env=False) as client:
        response = client.get(_supabase_endpoint(), headers=_supabase_headers(), params=params)
        response.raise_for_status()
        rows = response.json()

    entries: list[RunHistoryEntry] = []
    for row in rows:
        try:
            entries.append(RunHistoryEntry.model_validate(row["data"]))
        except (KeyError, ValueError):
            continue
    return entries


# --------------------------------------------------------------------------- #
# Local JSON backup (used when Supabase is unset or unreachable)
# --------------------------------------------------------------------------- #
def _read_local_entries() -> list[RunHistoryEntry]:
    path = history_file()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, list):
        return []

    entries: list[RunHistoryEntry] = []
    for item in payload:
        try:
            entries.append(RunHistoryEntry.model_validate(item))
        except ValueError:
            continue
    return entries


def _write_local_entries(entries: list[RunHistoryEntry]) -> None:
    payload = [entry.model_dump(mode="json") for entry in entries]
    history_file().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _append_local(entry: RunHistoryEntry) -> None:
    entries = _read_local_entries()
    entries.append(entry)
    _write_local_entries(entries[-500:])


def _list_local(limit: int, run_date: date | None) -> list[RunHistoryEntry]:
    entries = _read_local_entries()
    if run_date:
        entries = [entry for entry in entries if entry.run_date == run_date]
    entries.sort(key=lambda entry: entry.created_at, reverse=True)
    return entries[:limit]
