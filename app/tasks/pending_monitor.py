"""Review-queue workflow: discover articles into a pending queue, then accept/deny them.

- discover_to_pending(): find articles and add them to the shared Supabase queue
  WITHOUT uploading to Drive or building an email. Safe to run unattended (scheduler).
- accept_pending(): check Drive first; if the PDF is already there, skip entirely
  (no re-upload, no email). Otherwise upload it and build the email draft.
- deny_pending(): discard the item.
"""
import asyncio
import hashlib
from datetime import UTC, date, datetime, timedelta

import httpx

from app.config import get_settings
from app.schemas import ArticleResult, PendingArticle
from app.services import pending_store
from app.services.document_downloader import download_document_bytes
from app.services.email_draft import build_daily_email_message, save_daily_email_draft
from app.services.google_workspace import google_workspace_enabled, pdf_exists_in_drive
from app.services.imap_draft import imap_credentials_ready, save_imap_draft
from app.services import new_arrivals
from app.services.run_history import record_manual_history
from app.services.site_registry import (
    DiscoveredArticle,
    SiteConfig,
    discover_all_current_articles,
    discover_current_date_articles,
    get_sites,
)
from app.services.storage import safe_folder_name
from app.tasks.daily_monitor import (
    DOWNLOAD_HEADERS,
    _filename_from_url,
    _pdf_filename,
    _process_downloaded_item,
)


def _pending_id(site_remark: str, key: str, filename: str | None, run_date: date) -> str:
    raw = f"{site_remark}|{key}|{filename or ''}|{run_date.isoformat()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _run_date_path_parts(run_date: date) -> list[str]:
    return [
        str(run_date.year),
        run_date.strftime("%B %Y"),
        run_date.strftime("%d-%m-%Y"),
    ]


def _expected_pdf_name(item: PendingArticle) -> str:
    filename = item.filename or _filename_from_url(item.pdf_url) or _pdf_filename(item.title)
    if not filename.lower().endswith(".pdf"):
        filename = _pdf_filename(item.title)
    return safe_folder_name(filename)


def _find_site(site_remark: str) -> SiteConfig | None:
    target = site_remark.casefold()
    for site in get_sites():
        if site.remark.casefold() == target:
            return site
    return None


async def discover_to_pending(
    run_date: date, site_filters: list[str] | None = None, lookback_days: int = 0
) -> dict:
    """Discover articles for run_date (and the previous `lookback_days` days) into the queue.

    The look-back catches articles published while nobody was checking — e.g. over a
    weekend or holiday when the machine was off. De-duplication keeps overlapping days clean.
    """
    sites = get_sites(site_filters)
    lookback_days = max(0, lookback_days)
    dates = [run_date - timedelta(days=offset) for offset in range(lookback_days + 1)]
    items: dict[str, PendingArticle] = {}
    errors: list[str] = []

    def add_article(site: SiteConfig, article: DiscoveredArticle, fallback_date: date) -> None:
        bucket_date = article.published_date or fallback_date
        key = article.pdf_url or article.source_url
        pending_id = _pending_id(site.remark, key, article.filename, bucket_date)
        items[pending_id] = PendingArticle(
            id=pending_id,
            discovered_at=datetime.now(UTC),
            run_date=bucket_date,
            site_name=site.name,
            site_remark=site.remark,
            title=article.title,
            source_url=article.source_url,
            pdf_url=article.pdf_url,
            filename=article.filename,
            published_date=article.published_date,
            status="pending",
        )

    seen_records: list[dict] = []

    for site in sites:
        # Prefer identity-based "new arrivals" detection where the source supports a full
        # listing — this catches rows that appear late with an old date (BSE-style).
        all_current = None
        if new_arrivals.enabled():
            try:
                all_current = await discover_all_current_articles(site)
            except Exception as exc:
                errors.append(f"{site.remark} [all]: {exc}")
                all_current = None

        if all_current is not None:
            try:
                selection = new_arrivals.select_new(site.remark, all_current)
                for article in selection["surfaced"]:
                    add_article(site, article, run_date)
                seen_records.extend(selection["records"])
                continue  # handled by new-arrivals; skip the date-based path
            except Exception as exc:
                errors.append(f"{site.remark} [new-arrivals]: {exc}")
                # fall through to date-based detection below

        # Date-based detection (today + look-back window) for every other source.
        for check_date in dates:
            try:
                discovered = await discover_current_date_articles(check_date, site)
            except Exception as exc:
                errors.append(f"{site.remark} [{check_date.isoformat()}]: {exc}")
                continue

            for article in discovered:
                if article.published_date is not None and article.published_date != check_date:
                    continue
                if article.published_date is None and check_date != run_date:
                    continue
                add_article(site, article, check_date)

    added = pending_store.add_pending(list(items.values())) if items else 0
    # Record fingerprints as seen ONLY after the queue insert succeeded, so a failed insert
    # never marks an unqueued article as "already seen".
    if seen_records:
        try:
            new_arrivals.commit_seen(seen_records)
        except Exception as exc:
            errors.append(f"seen-commit: {exc}")

    return {
        "discovered": len(items),
        "added": added,
        "errors": errors,
        "dates_checked": [d.isoformat() for d in dates],
    }


async def accept_pending(pending_id: str) -> dict:
    item = pending_store.get_pending(pending_id)
    if not item:
        return {"ok": False, "error": "Pending item not found."}
    if item.status != "pending":
        return {"ok": False, "error": f"Item already {item.status}."}

    site = _find_site(item.site_remark)
    if not site:
        return {"ok": False, "error": f"Source '{item.site_remark}' is no longer configured."}

    run_date = item.run_date
    parts = _run_date_path_parts(run_date)

    # Check Drive first — if it's already there, skip entirely (no re-upload, no email).
    if google_workspace_enabled():
        try:
            existing = pdf_exists_in_drive(parts, item.site_remark, _expected_pdf_name(item))
        except Exception:
            existing = None
        if existing:
            pending_store.update_fields(pending_id, {"status": "skipped", "drive_pdf_url": existing})
            record_manual_history(
                run_date=run_date,
                sources=[item.site_remark],
                articles=[
                    ArticleResult(
                        site_name=item.site_name,
                        site_remark=item.site_remark,
                        title=item.title,
                        source_url=item.source_url,
                        published_date=item.published_date,
                        drive_pdf_url=existing,
                        summary=item.summary,
                    )
                ],
                upload_drive=True,
                save_webmail_draft=False,
                send_email=False,
                status="completed",
                message="Pending article already existed in Drive and was skipped.",
            )
            return {
                "ok": True,
                "skipped": True,
                "drive_pdf_url": existing,
                "message": "Already in Drive — skipped (excluded from the email).",
            }

    # Not in Drive: download from source and upload to Drive. Email is built later, in bulk.
    discovered = DiscoveredArticle(
        site=site,
        title=item.title,
        source_url=item.source_url,
        pdf_url=item.pdf_url,
        published_date=item.published_date,
        filename=item.filename,
    )
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=settings.download_timeout_seconds, follow_redirects=True, trust_env=False
    ) as client:
        content = await download_document_bytes(client, discovered, DOWNLOAD_HEADERS)

    articles = _process_downloaded_item(run_date, discovered, content, upload_drive=True)
    if not articles:
        return {"ok": False, "error": "No PDF could be produced from the source."}

    primary = articles[0]
    pending_store.update_fields(
        pending_id,
        {
            "status": "accepted",
            "title": primary.title,
            "summary": primary.summary,
            "drive_pdf_url": primary.drive_pdf_url,
            "drive_folder_url": primary.drive_folder_url,
        },
    )
    record_manual_history(
        run_date=run_date,
        sources=[item.site_remark],
        articles=articles,
        upload_drive=True,
        save_webmail_draft=False,
        send_email=False,
        status="completed",
        message="Pending article accepted and uploaded to Drive.",
    )
    return {
        "ok": True,
        "skipped": False,
        "drive_pdf_url": primary.drive_pdf_url,
        "message": "Accepted — uploaded to Drive. Click 'Build email' to create the combined draft.",
    }


def build_email_for_date(run_date: date | None = None) -> dict:
    """Build one combined email draft from every currently accepted article."""
    items = pending_store.list_accepted()
    if not items:
        return {"ok": False, "error": "No accepted articles are available yet."}

    draft_date = run_date or max(item.run_date for item in items)

    articles = [
        ArticleResult(
            site_name=item.site_name,
            site_remark=item.site_remark,
            title=item.title,
            source_url=item.source_url,
            published_date=item.published_date,
            drive_pdf_url=item.drive_pdf_url,
            drive_folder_url=item.drive_folder_url,
            summary=item.summary,
        )
        for item in items
    ]

    first_date = min(item.run_date for item in items)
    last_date = max(item.run_date for item in items)
    if first_date == last_date:
        date_text = first_date.strftime("%d-%m-%Y")
        file_label = date_text
    else:
        date_text = f"{first_date.strftime('%d-%m-%Y')} to {last_date.strftime('%d-%m-%Y')}"
        file_label = f"{first_date.strftime('%d-%m-%Y')}_to_{last_date.strftime('%d-%m-%Y')}"

    settings = get_settings()
    draft_path = save_daily_email_draft(
        draft_date,
        articles,
        file_label=file_label,
        date_label=date_text,
    )
    webmail_saved = False
    webmail_error = None
    if imap_credentials_ready(settings.email_credentials_file):
        try:
            webmail_saved = save_imap_draft(
                build_daily_email_message(draft_date, articles, date_label=date_text),
                settings.email_credentials_file,
            )
        except Exception as exc:
            webmail_error = str(exc)

    message = f"Built one combined draft from {len(articles)} accepted article(s) across {date_text}."
    if draft_path:
        message = f"{message} Local draft: {draft_path}"
    if webmail_saved:
        message = f"{message} Hostinger draft saved."
    elif webmail_error:
        message = f"{message} Hostinger draft failed: {webmail_error}"

    record_manual_history(
        run_date=draft_date,
        sources=sorted({article.site_remark for article in articles}),
        articles=articles,
        upload_drive=True,
        save_webmail_draft=True,
        send_email=False,
        status="issues" if webmail_error else "completed",
        message=message,
        draft_path=draft_path,
        webmail_draft_saved=webmail_saved,
        webmail_draft_error=webmail_error,
    )

    return {
        "ok": True,
        "count": len(articles),
        "draft_path": str(draft_path) if draft_path else None,
        "webmail_draft_saved": webmail_saved,
        "webmail_draft_error": webmail_error,
        "message": message,
    }


def deny_pending(pending_id: str) -> dict:
    item = pending_store.get_pending(pending_id)
    if not item:
        return {"ok": False, "error": "Pending item not found."}
    pending_store.set_status(pending_id, "denied")
    return {"ok": True, "message": "Denied."}


def main() -> None:
    """Entry point for a scheduled task: check today plus the look-back window."""
    lookback = get_settings().pending_lookback_days
    result = asyncio.run(discover_to_pending(date.today(), lookback_days=lookback))
    print(result)


if __name__ == "__main__":
    main()
