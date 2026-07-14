"""Identity-based 'new since seen' detection.

Some sources (e.g. BSE NOC) add a row to their page days after its printed date, so
date-based matching misses it. This module instead remembers a stable fingerprint of
every row we have ever encountered and surfaces only rows we have not seen before —
independent of date or list ordering.

A per-source baseline is automatic: the first time a source is seen, every current row is
recorded silently (returns nothing), so the first run never floods the queue. After that,
only genuinely new rows are returned.
"""
import hashlib
from datetime import UTC, datetime

from app.services import supabase_client
from app.services.site_registry import DiscoveredArticle


SEEN_TABLE = "seen_articles"


def enabled() -> bool:
    return supabase_client.enabled()


def _fingerprint(site_remark: str, article: DiscoveredArticle) -> str:
    identity = article.pdf_url or article.source_url or ""
    raw = f"{site_remark}|{identity}|{article.filename or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


_PAGE = 1000


def _all_seen_fingerprints(site_remark: str) -> set[str]:
    fingerprints: set[str] = set()
    offset = 0
    while True:
        rows = supabase_client.select(
            SEEN_TABLE,
            {
                "select": "fingerprint",
                "site_remark": f"eq.{site_remark}",
                "order": "fingerprint.asc",
                "limit": str(_PAGE),
                "offset": str(offset),
            },
        )
        fingerprints.update(row["fingerprint"] for row in rows if row.get("fingerprint"))
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return fingerprints


def _record(rows: list[dict]) -> None:
    # Insert in batches so a large baseline (thousands of rows) stays within request limits.
    for start in range(0, len(rows), _PAGE):
        supabase_client.insert(SEEN_TABLE, rows[start : start + _PAGE], ignore_duplicates=True)


def select_new(
    site_remark: str, articles: list[DiscoveredArticle], baseline_seed: int = 25
) -> dict:
    """Decide which articles are new WITHOUT writing anything yet.

    Returns {"surfaced": [...], "records": [...], "baseline": bool}. The caller queues the
    surfaced articles, then calls commit_seen(records) ONLY after that succeeds — so a failed
    queue insert can never poison the baseline.

    On the first (baseline) run there is no prior snapshot, so the `baseline_seed`
    most-recently-listed rows are surfaced (that is where just-appeared rows sit), while every
    current row is recorded. Afterwards, only rows never seen before are surfaced.
    """
    if not articles:
        return {"surfaced": [], "records": [], "baseline": False}

    # De-duplicate within the current page by fingerprint, preserving list (recency) order.
    current: dict[str, DiscoveredArticle] = {}
    for article in articles:
        current[_fingerprint(site_remark, article)] = article

    seen = _all_seen_fingerprints(site_remark)
    is_baseline = len(seen) == 0

    now = datetime.now(UTC).isoformat()
    records = [
        {
            "fingerprint": fp,
            "site_remark": site_remark,
            "title": article.title,
            "published_date": article.published_date.isoformat() if article.published_date else None,
            "first_seen": now,
        }
        for fp, article in current.items()
        if fp not in seen
    ]

    if is_baseline:
        surfaced = list(current.values())[:baseline_seed]
    else:
        surfaced = [current[record["fingerprint"]] for record in records]

    return {"surfaced": surfaced, "records": records, "baseline": is_baseline}


def commit_seen(records: list[dict]) -> None:
    """Record fingerprints as seen. Call only after the surfaced items were queued successfully."""
    _record(records)
