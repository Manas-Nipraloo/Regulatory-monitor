"""Shared queue of discovered-but-unreviewed articles, stored in Supabase."""
from datetime import UTC, date, datetime

from app.schemas import PendingArticle
from app.services import supabase_client


PENDING_TABLE = "pending_articles"


def enabled() -> bool:
    return supabase_client.enabled()


def add_pending(items: list[PendingArticle]) -> int:
    """Insert new pending items, skipping any that already exist. Returns count added."""
    if not items:
        return 0
    rows = [item.model_dump(mode="json") for item in items]
    inserted = supabase_client.insert(
        PENDING_TABLE, rows, ignore_duplicates=True, return_rows=True
    )
    return len(inserted or [])


def list_pending(status: str = "pending", limit: int = 200) -> list[PendingArticle]:
    params = {
        "select": "*",
        "order": "discovered_at.desc",
        "limit": str(max(1, min(limit, 500))),
    }
    if status:
        params["status"] = f"eq.{status}"
    rows = supabase_client.select(PENDING_TABLE, params)
    items: list[PendingArticle] = []
    for row in rows:
        try:
            items.append(PendingArticle.model_validate(row))
        except ValueError:
            continue
    return items


def get_pending(pending_id: str) -> PendingArticle | None:
    rows = supabase_client.select(
        PENDING_TABLE, {"select": "*", "id": f"eq.{pending_id}", "limit": "1"}
    )
    if not rows:
        return None
    try:
        return PendingArticle.model_validate(rows[0])
    except ValueError:
        return None


def set_status(pending_id: str, status: str, drive_pdf_url: str | None = None) -> None:
    values: dict = {"status": status, "decided_at": datetime.now(UTC).isoformat()}
    if drive_pdf_url is not None:
        values["drive_pdf_url"] = drive_pdf_url
    supabase_client.update(PENDING_TABLE, {"id": f"eq.{pending_id}"}, values)


def update_fields(pending_id: str, values: dict) -> None:
    payload = {**values, "decided_at": datetime.now(UTC).isoformat()}
    supabase_client.update(PENDING_TABLE, {"id": f"eq.{pending_id}"}, payload)


def list_accepted(run_date: date | None = None) -> list[PendingArticle]:
    params = {
        "select": "*",
        "status": "eq.accepted",
        "order": "run_date.asc,discovered_at.asc",
    }
    if run_date is not None:
        params["run_date"] = f"eq.{run_date.isoformat()}"
    rows = supabase_client.select(PENDING_TABLE, params)
    items: list[PendingArticle] = []
    for row in rows:
        try:
            items.append(PendingArticle.model_validate(row))
        except ValueError:
            continue
    return items
