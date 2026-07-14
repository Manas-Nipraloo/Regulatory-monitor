from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import httpx
import re

from app.config import get_settings
from app.schemas import ArticleResult, DailyRunResponse
from app.services.document_downloader import download_document_bytes
from app.services.email_draft import build_daily_email_message, save_daily_email_draft
from app.services.google_workspace import google_workspace_enabled, upload_pdf_bytes_to_drive
from app.services.imap_draft import imap_credentials_ready, save_imap_draft
from app.services.pdf_extractor import extract_pdf_metadata
from app.services.site_registry import DiscoveredArticle, discover_current_date_articles, get_sites
from app.services.smtp_email import send_smtp_email, smtp_credentials_ready
from app.services.storage import safe_folder_name


DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/pdf,application/zip,application/octet-stream,*/*",
    "Referer": "https://www.bseindia.com/",
}


async def run_daily_monitor(
    run_date: date,
    site_filters: list[str] | None = None,
    upload_drive: bool = True,
    save_webmail_draft: bool = False,
    send_email_enabled: bool = False,
) -> DailyRunResponse:
    settings = get_settings()
    sites = get_sites(site_filters)
    articles: list[ArticleResult] = []
    errors: list[str] = []

    async with httpx.AsyncClient(
        timeout=settings.download_timeout_seconds,
        follow_redirects=True,
        trust_env=False,
    ) as client:
        for site in sites:
            try:
                discovered = await discover_current_date_articles(run_date, site)
            except Exception as exc:
                errors.append(f"{site.remark}: source check failed ({exc})")
                continue

            for item in discovered:
                if item.published_date and item.published_date != run_date:
                    continue

                try:
                    content = await download_document_bytes(client, item, DOWNLOAD_HEADERS)
                    articles.extend(_process_downloaded_item(run_date, item, content, upload_drive=upload_drive))
                except Exception as exc:
                    errors.append(f"{site.remark}: {item.title} failed ({exc})")

    draft_path = save_daily_email_draft(run_date, articles) if articles else None
    webmail_draft_saved = False
    webmail_draft_error = None
    if articles and save_webmail_draft and imap_credentials_ready(settings.email_credentials_file):
        try:
            webmail_draft_saved = save_imap_draft(
                build_daily_email_message(run_date, articles),
                settings.email_credentials_file,
            )
        except Exception as exc:
            webmail_draft_error = str(exc)

    email_sent = False
    email_error = None
    if articles and send_email_enabled and smtp_credentials_ready(settings.email_credentials_file):
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
    if errors:
        message = f"{message} Issues: {'; '.join(errors[:3])}"
        if len(errors) > 3:
            message = f"{message}; plus {len(errors) - 3} more."

    return DailyRunResponse(
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


def _process_downloaded_item(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
) -> list[ArticleResult]:
    return list(_process_downloaded_item_incremental(run_date, item, content, upload_drive=upload_drive))


def _process_downloaded_item_incremental(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
):
    filename = item.filename or _filename_from_url(item.pdf_url) or _pdf_filename(item.title)

    if _looks_like_zip(filename, content):
        yield from _process_zip_incremental(run_date, item, content, upload_drive=upload_drive)
        return

    if _looks_like_pdf(filename, content):
        pdf_name = safe_folder_name(filename if filename.lower().endswith(".pdf") else _pdf_filename(item.title))
        yield _article_from_pdf(run_date, item, pdf_name, content, upload_drive=upload_drive)


def _process_zip(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
) -> list[ArticleResult]:
    return list(_process_zip_incremental(run_date, item, content, upload_drive=upload_drive))


def _process_zip_incremental(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
):
    try:
        archive = ZipFile(BytesIO(content))
    except BadZipFile:
        return

    for member in archive.infolist():
        if member.is_dir() or not member.filename.lower().endswith(".pdf"):
            continue
        pdf_name = safe_folder_name(Path(member.filename).name)
        pdf_content = archive.read(member)
        yield _article_from_pdf(run_date, item, pdf_name, pdf_content, upload_drive=upload_drive)


def _article_from_pdf(
    run_date: date,
    item: DiscoveredArticle,
    pdf_name: str,
    content: bytes,
    upload_drive: bool = True,
) -> ArticleResult:
    metadata = extract_pdf_metadata(filename=pdf_name, content=content)
    drive_pdf_url = None
    drive_folder_url = None
    if upload_drive and google_workspace_enabled():
        drive_upload = upload_pdf_bytes_to_drive(
            filename=pdf_name,
            content=content,
            run_date_path_parts=[
                str(run_date.year),
                run_date.strftime("%B %Y"),
                run_date.strftime("%d-%m-%Y"),
            ],
            site_remark=item.site.remark,
        )
        drive_pdf_url = drive_upload.pdf_url
        drive_folder_url = drive_upload.folder_url

    return ArticleResult(
        site_name=item.site.name,
        site_remark=item.site.remark,
        title=_email_title(item, pdf_name, metadata.heading),
        source_url=item.source_url,
        published_date=item.published_date,
        pdf_path=None,
        drive_pdf_url=drive_pdf_url,
        drive_folder_url=drive_folder_url,
        metadata_path=None,
        summary=_email_summary(item, pdf_name, metadata.summary),
    )


def _email_title(item: DiscoveredArticle, pdf_name: str, extracted_heading: str) -> str:
    if not _bad_extracted_heading(extracted_heading, pdf_name):
        return extracted_heading
    return item.title or Path(pdf_name).stem


def _email_summary(item: DiscoveredArticle, pdf_name: str, extracted_summary: str) -> str:
    if _unreadable_summary(extracted_summary):
        date_text = item.published_date.strftime("%B %d, %Y") if item.published_date else "the selected date"
        pdf_title = item.title or Path(pdf_name).stem
        return (
            f"{item.site.remark} published '{pdf_title}' on {date_text}. The uploaded PDF should be reviewed for detailed clauses "
            "because the source scan was not clear enough for reliable text extraction."
        )
    return extracted_summary


def _bad_extracted_heading(heading: str, pdf_name: str) -> bool:
    cleaned = " ".join((heading or "").split()).strip()
    if not cleaned:
        return True

    filename_stem = Path(pdf_name).stem.casefold()
    if cleaned.casefold() == filename_stem:
        return True

    compact = re.sub(r"[^A-Za-z0-9]", "", cleaned)
    if len(compact) >= 24 and " " not in cleaned:
        alpha_num_ratio = len(compact) / max(len(cleaned), 1)
        return alpha_num_ratio > 0.85
    return False


def _unreadable_summary(summary: str) -> bool:
    text = " ".join((summary or "").split()).casefold()
    return (
        not text
        or "could not be read clearly enough" in text
        or text == "no extractable text was found in this pdf."
        or text.startswith("reserve bank of india")
        or text.startswith("rbi/")
        or text.startswith("0 reserve bank of india")
    )


def _looks_like_zip(filename: str, content: bytes) -> bool:
    return filename.lower().endswith(".zip") or content.startswith(b"PK\x03\x04")


def _looks_like_pdf(filename: str, content: bytes) -> bool:
    return filename.lower().endswith(".pdf") or content.startswith(b"%PDF")


def _filename_from_url(url: str) -> str:
    return Path(url.split("?", 1)[0]).name


def _pdf_filename(title: str) -> str:
    cleaned = " ".join(title.split()).strip() or "article"
    return f"{cleaned[:120]}.pdf"
