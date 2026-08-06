from datetime import date
import json
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.schemas import (
    DailyRunRequest,
    DailyRunResponse,
    ManualDraftRequest,
    PdfExtractionResponse,
    RunHistoryEntry,
)
from app.services.document_downloader import download_document_bytes
from app.services.email_draft import build_daily_email_message, save_daily_email_draft
from app.services.imap_draft import imap_credentials_ready, save_imap_draft
from app.services.pdf_extractor import extract_pdf_metadata
from app.services.run_history import list_history, record_history, record_manual_history
from app.services.site_registry import DiscoveredArticle, SiteConfig, discover_current_date_articles, get_sites
from app.services.smtp_email import send_smtp_email, smtp_credentials_ready
from app.services.storage import safe_folder_name
from app.services import pending_store
from app.tasks.daily_monitor import (
    DOWNLOAD_HEADERS,
    SkippedExistingPdf,
    _article_from_pdf,
    _existing_drive_pdf_url,
    _pdf_filename,
    _process_downloaded_item_incremental_with_skips,
    run_daily_monitor,
)
from app.tasks.pending_monitor import accept_pending, build_email_for_date, deny_pending, discover_to_pending


settings = get_settings()

app = FastAPI(title=settings.app_name)
WEB_DIR = Path(__file__).resolve().parent / "web"

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/history-page")
def history_page() -> FileResponse:
    return FileResponse(WEB_DIR / "history.html", media_type="text/html; charset=utf-8")


@app.get("/manual-page")
def manual_page() -> FileResponse:
    return FileResponse(WEB_DIR / "manual.html", media_type="text/html; charset=utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@app.post("/tasks/daily-run", response_model=DailyRunResponse)
async def daily_run(request: DailyRunRequest) -> DailyRunResponse:
    run_date = request.run_date or date.today()

    response = await run_daily_monitor(
        run_date=run_date,
        site_filters=request.site_filters,
        upload_drive=request.upload_drive,
        save_webmail_draft=request.save_webmail_draft,
        send_email_enabled=request.send_email,
    )

    sources = [site.remark for site in get_sites(request.site_filters)]
    record_history(response, sources, request.upload_drive, request.save_webmail_draft, request.send_email)

    return response


@app.post("/tasks/daily-run-stream")
async def daily_run_stream(request: DailyRunRequest) -> StreamingResponse:
    run_date = request.run_date or date.today()

    async def events():
        articles = []
        errors: list[str] = []
        skipped_existing = 0
        sites = get_sites(request.site_filters)

        yield _json_line({"type": "start", "site_count": len(sites)})

        async with httpx.AsyncClient(
            timeout=settings.download_timeout_seconds,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            for site in sites:
                yield _json_line({"type": "source", "remark": site.remark, "status": "checking"})

                try:
                    discovered = await discover_current_date_articles(run_date, site)
                except Exception as exc:
                    error = f"{site.remark}: source check failed ({exc})"
                    errors.append(error)
                    yield _json_line({"type": "error", "message": error})
                    continue

                yield _json_line(
                    {
                        "type": "source",
                        "remark": site.remark,
                        "status": "matched",
                        "count": len(discovered),
                    }
                )

                for index, item in enumerate(discovered, start=1):
                    if item.published_date and item.published_date != run_date:
                        continue

                    if request.upload_drive:
                        existing = _existing_drive_pdf_url(run_date, item)

                        if existing:
                            skipped_existing += 1
                            yield _json_line(
                                {
                                    "type": "document",
                                    "status": "skipped_existing",
                                    "index": index,
                                    "total": len(discovered),
                                    "title": item.title,
                                    "site_remark": site.remark,
                                    "drive_pdf_url": existing,
                                }
                            )
                            continue

                    yield _json_line(
                        {
                            "type": "document",
                            "status": "downloading",
                            "index": index,
                            "total": len(discovered),
                            "title": item.title,
                            "site_remark": site.remark,
                        }
                    )

                    try:
                        content = await download_document_bytes(client, item, DOWNLOAD_HEADERS)

                        yield _json_line(
                            {
                                "type": "document",
                                "status": "processing",
                                "index": index,
                                "total": len(discovered),
                                "title": item.title,
                                "size": len(content),
                            }
                        )

                        for result in _process_downloaded_item_incremental_with_skips(
                            run_date,
                            item,
                            content,
                            upload_drive=request.upload_drive,
                        ):
                            if isinstance(result, SkippedExistingPdf):
                                skipped_existing += 1
                                yield _json_line(
                                    {
                                        "type": "document",
                                        "status": "skipped_existing",
                                        "index": index,
                                        "total": len(discovered),
                                        "title": result.pdf_name,
                                        "site_remark": site.remark,
                                        "drive_pdf_url": result.drive_pdf_url,
                                    }
                                )
                                continue

                            articles.append(result)

                            yield _json_line(
                                {
                                    "type": "article",
                                    "count": len(articles),
                                    "article": result.model_dump(mode="json"),
                                }
                            )

                    except Exception as exc:
                        error = f"{site.remark}: {item.title} failed ({exc})"
                        errors.append(error)
                        yield _json_line({"type": "error", "message": error})

        draft_path = save_daily_email_draft(run_date, articles) if articles else None

        webmail_draft_saved = False
        webmail_draft_error = None

        if articles and request.save_webmail_draft and imap_credentials_ready(settings.email_credentials_file):
            try:
                webmail_draft_saved = save_imap_draft(
                    build_daily_email_message(run_date, articles),
                    settings.email_credentials_file,
                )
            except Exception as exc:
                webmail_draft_error = str(exc)

        email_sent = False
        email_error = None

        if articles and request.send_email and smtp_credentials_ready(settings.email_credentials_file):
            try:
                email_sent = send_smtp_email(
                    build_daily_email_message(run_date, articles),
                    settings.email_credentials_file,
                )
            except Exception as exc:
                email_error = str(exc)

        message = "Daily run completed."

        if not articles:
            message = "No current-date articles found. No Drive upload or email draft was created."
        elif email_sent:
            message = "Daily run completed and email sent."
        elif email_error:
            message = f"Daily run completed, but email sending failed: {email_error}"

        if skipped_existing:
            message = f"{message} Skipped {skipped_existing} PDF(s) already present in Drive."

        if errors:
            message = f"{message} Issues: {'; '.join(errors[:3])}"

            if len(errors) > 3:
                message = f"{message}; plus {len(errors) - 3} more."

        response = DailyRunResponse(
            run_date=run_date,
            articles_found=len(articles),
            articles=articles,
            draft_path=draft_path,
            webmail_draft_saved=webmail_draft_saved,
            webmail_draft_error=webmail_draft_error,
            email_sent=email_sent,
            email_error=email_error,
            message=message,
        )

        record_history(
            response,
            [site.remark for site in sites],
            request.upload_drive,
            request.save_webmail_draft,
            request.send_email,
        )

        yield _json_line(
            {
                "type": "done",
                "run_date": run_date.isoformat(),
                "articles_found": len(articles),
                "draft_path": str(draft_path) if draft_path else None,
                "webmail_draft_saved": webmail_draft_saved,
                "webmail_draft_error": webmail_draft_error,
                "email_sent": email_sent,
                "email_error": email_error,
                "message": message,
            }
        )

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _json_line(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


@app.get("/sites")
def sites() -> list[dict[str, str]]:
    return [
        {
            "name": site.name,
            "remark": site.remark,
            "url": site.url,
        }
        for site in get_sites()
    ]


@app.get("/history", response_model=list[RunHistoryEntry])
def history(limit: int = 50, run_date: date | None = None) -> list[RunHistoryEntry]:
    return list_history(limit, run_date)


@app.get("/pending")
def pending_list() -> list[dict[str, object]]:
    if not pending_store.enabled():
        return []

    try:
        window = get_settings().pending_display_window_days
        return [
            item.model_dump(mode="json")
            for item in pending_store.list_pending(max_age_days=window)
        ]
    except Exception:
        return []


@app.post("/pending/refresh")
async def pending_refresh(request: DailyRunRequest) -> dict[str, object]:
    if not pending_store.enabled():
        return {"discovered": 0, "added": 0, "errors": ["Supabase is not configured."]}

    run_date = request.run_date or date.today()
    lookback = get_settings().pending_lookback_days

    return await discover_to_pending(run_date, request.site_filters or None, lookback_days=lookback)


@app.post("/pending/{pending_id}/accept")
async def pending_accept(pending_id: str) -> dict[str, object]:
    return await accept_pending(pending_id)


@app.post("/pending/{pending_id}/deny")
def pending_deny(pending_id: str) -> dict[str, object]:
    return deny_pending(pending_id)


@app.post("/pending/build-email")
def pending_build_email(request: DailyRunRequest) -> dict[str, object]:
    try:
        return build_email_for_date(
            run_date=request.run_date,
            site_filters=request.site_filters or None,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Build email failed: {exc}"}


@app.post("/pdfs/extract", response_model=PdfExtractionResponse)
async def extract_pdf(file: UploadFile = File(...)) -> PdfExtractionResponse:
    content = await file.read()

    return extract_pdf_metadata(filename=file.filename or "uploaded.pdf", content=content)


@app.post("/manual/summarize")
async def manual_summarize(
    file: UploadFile = File(...),
    published_date: str = Form(...),
    title: str = Form(""),
    site_remark: str = Form("GOI income tax website"),
) -> dict[str, object]:
    """For sources the automated scrapers can't reach: process a manually-downloaded
    PDF through the same extract + Drive-upload pipeline used for discovered articles."""
    try:
        parsed_date = date.fromisoformat(published_date)
    except ValueError:
        return {"ok": False, "error": "Invalid date. Use YYYY-MM-DD."}

    content = await file.read()
    if not content:
        return {"ok": False, "error": "Uploaded file is empty."}

    raw_name = file.filename or "document.pdf"
    pdf_name = safe_folder_name(
        raw_name if raw_name.lower().endswith(".pdf") else _pdf_filename(title.strip() or Path(raw_name).stem)
    )
    remark = site_remark.strip() or "GOI income tax website"

    site = SiteConfig(
        name="Manual Upload", remark=remark, url="https://www.incometaxindia.gov.in/"
    )
    item = DiscoveredArticle(
        site=site,
        title=title.strip(),
        source_url=site.url,
        pdf_url="",
        published_date=parsed_date,
        filename=raw_name,
    )

    try:
        result = _article_from_pdf(parsed_date, item, pdf_name, content, upload_drive=True)
    except Exception as exc:
        return {"ok": False, "error": f"Processing failed: {exc}"}

    return {"ok": True, "article": result.model_dump(mode="json")}


def _manual_draft_labels(
    articles: list, run_date: date | None
) -> tuple[date, str, str]:
    """Shared date/subject-label math for the manual draft + preview endpoints."""
    dated = [a.published_date for a in articles if a.published_date]
    first_date = min(dated) if dated else date.today()
    last_date = max(dated) if dated else first_date
    draft_date = run_date or last_date

    if first_date == last_date:
        date_text = first_date.strftime("%d-%m-%Y")
        file_label = date_text
    else:
        date_text = f"{first_date.strftime('%d-%m-%Y')} to {last_date.strftime('%d-%m-%Y')}"
        file_label = f"{first_date.strftime('%d-%m-%Y')}_to_{last_date.strftime('%d-%m-%Y')}"

    return draft_date, date_text, file_label


@app.post("/manual/preview-email")
def manual_preview_email(request: ManualDraftRequest) -> dict[str, object]:
    articles = request.articles
    if not articles:
        return {"ok": False, "error": "No articles to preview."}

    draft_date, date_text, _ = _manual_draft_labels(articles, request.run_date)
    message = build_daily_email_message(draft_date, articles, date_label=date_text)
    html_part = message.get_body(preferencelist=("html",))

    return {
        "ok": True,
        "subject": str(message["Subject"] or ""),
        "to": str(message["To"] or ""),
        "cc": str(message["Cc"] or ""),
        "html": html_part.get_content() if html_part else "",
    }


@app.post("/manual/draft-email")
def manual_draft_email(request: ManualDraftRequest) -> dict[str, object]:
    articles = request.articles
    if not articles:
        return {"ok": False, "error": "No articles to draft."}

    draft_date, date_text, file_label = _manual_draft_labels(articles, request.run_date)

    draft_path = save_daily_email_draft(draft_date, articles, file_label=file_label, date_label=date_text)

    webmail_saved = False
    webmail_error = None
    settings = get_settings()
    if imap_credentials_ready(settings.email_credentials_file):
        try:
            webmail_saved = save_imap_draft(
                build_daily_email_message(draft_date, articles, date_label=date_text),
                settings.email_credentials_file,
            )
        except Exception as exc:
            webmail_error = str(exc)

    message = f"Built a manual draft from {len(articles)} uploaded article(s) across {date_text}."
    if draft_path:
        message = f"{message} Local draft: {draft_path}"
    if webmail_saved:
        message = f"{message} Hostinger draft saved."
    elif webmail_error:
        message = f"{message} Hostinger draft failed: {webmail_error}"

    record_manual_history(
        run_date=draft_date,
        sources=sorted({a.site_remark for a in articles}),
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