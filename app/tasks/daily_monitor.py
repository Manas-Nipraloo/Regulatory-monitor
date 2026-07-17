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
from app.services.google_workspace import (
    google_workspace_enabled,
    pdf_exists_in_drive,
    upload_pdf_bytes_to_drive,
)
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
    skipped_existing = 0

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
                    if upload_drive and google_workspace_enabled():
                        existing = _existing_drive_pdf_url(run_date, item)
                        if existing:
                            skipped_existing += 1
                            continue

                    content = await download_document_bytes(client, item, DOWNLOAD_HEADERS)

                    for result in _process_downloaded_item_incremental_with_skips(
                        run_date,
                        item,
                        content,
                        upload_drive=upload_drive,
                    ):
                        if isinstance(result, SkippedExistingPdf):
                            skipped_existing += 1
                            continue

                        articles.append(result)

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

    if skipped_existing:
        suffix = f" Skipped {skipped_existing} PDF(s) already present in Drive."
        message = f"{message}{suffix}"

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


class SkippedExistingPdf:
    def __init__(self, pdf_name: str, drive_pdf_url: str | None = None):
        self.pdf_name = pdf_name
        self.drive_pdf_url = drive_pdf_url


def _process_downloaded_item(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
) -> list[ArticleResult]:
    articles: list[ArticleResult] = []

    for result in _process_downloaded_item_incremental_with_skips(
        run_date,
        item,
        content,
        upload_drive=upload_drive,
    ):
        if isinstance(result, ArticleResult):
            articles.append(result)

    return articles


def _process_downloaded_item_incremental(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
):
    for result in _process_downloaded_item_incremental_with_skips(
        run_date,
        item,
        content,
        upload_drive=upload_drive,
    ):
        if isinstance(result, ArticleResult):
            yield result


def _process_downloaded_item_incremental_with_skips(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
):
    filename = item.filename or _filename_from_url(item.pdf_url) or _pdf_filename(item.title)

    if _looks_like_zip(filename, content):
        yield from _process_zip_incremental_with_skips(
            run_date,
            item,
            content,
            upload_drive=upload_drive,
        )
        return

    if _looks_like_pdf(filename, content):
        pdf_name = safe_folder_name(
            filename if filename.lower().endswith(".pdf") else _pdf_filename(item.title)
        )

        if upload_drive and google_workspace_enabled():
            existing = _existing_drive_pdf_url_by_name(run_date, item.site.remark, pdf_name)
            if existing:
                yield SkippedExistingPdf(pdf_name=pdf_name, drive_pdf_url=existing)
                return

        yield _article_from_pdf(
            run_date,
            item,
            pdf_name,
            content,
            upload_drive=upload_drive,
        )


def _process_zip(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
) -> list[ArticleResult]:
    articles: list[ArticleResult] = []

    for result in _process_zip_incremental_with_skips(
        run_date,
        item,
        content,
        upload_drive=upload_drive,
    ):
        if isinstance(result, ArticleResult):
            articles.append(result)

    return articles


def _process_zip_incremental(
    run_date: date,
    item: DiscoveredArticle,
    content: bytes,
    upload_drive: bool = True,
):
    for result in _process_zip_incremental_with_skips(
        run_date,
        item,
        content,
        upload_drive=upload_drive,
    ):
        if isinstance(result, ArticleResult):
            yield result


def _process_zip_incremental_with_skips(
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

        if upload_drive and google_workspace_enabled():
            existing = _existing_drive_pdf_url_by_name(run_date, item.site.remark, pdf_name)
            if existing:
                yield SkippedExistingPdf(pdf_name=pdf_name, drive_pdf_url=existing)
                continue

        yield _article_from_pdf(
            run_date,
            item,
            pdf_name,
            pdf_content,
            upload_drive=upload_drive,
        )


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
            run_date_path_parts=_run_date_path_parts(run_date),
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


def _run_date_path_parts(run_date: date) -> list[str]:
    return [
        str(run_date.year),
        run_date.strftime("%B %Y"),
        run_date.strftime("%d-%m-%Y"),
    ]


def _existing_drive_pdf_url(run_date: date, item: DiscoveredArticle) -> str | None:
    pdf_name = _expected_pdf_filename(item)

    if not pdf_name:
        return None

    return _existing_drive_pdf_url_by_name(run_date, item.site.remark, pdf_name)


def _existing_drive_pdf_url_by_name(run_date: date, site_remark: str, pdf_name: str) -> str | None:
    try:
        return pdf_exists_in_drive(_run_date_path_parts(run_date), site_remark, pdf_name)
    except Exception:
        return None


def _expected_pdf_filename(item: DiscoveredArticle) -> str | None:
    filename = item.filename or _filename_from_url(item.pdf_url)

    if not filename:
        return None

    if not _looks_like_pdf(filename, b""):
        return None

    return safe_folder_name(
        filename if filename.lower().endswith(".pdf") else _pdf_filename(item.title)
    )


def _email_title(item: DiscoveredArticle, pdf_name: str, extracted_heading: str) -> str:
    if not _bad_extracted_heading(extracted_heading, pdf_name):
        return extracted_heading

    return item.title or Path(pdf_name).stem


def _email_summary(item: DiscoveredArticle, pdf_name: str, extracted_summary: str) -> str:
    summary = " ".join((extracted_summary or "").split()).strip()

    if not summary:
        return ""

    blocked_fallbacks = (
        "no extractable text was found in this pdf.",
        "this scanned pdf could not be read clearly enough",
        "please review the uploaded document",
        "source scan was not clear enough",
        "the uploaded pdf should be reviewed",
        "summary could not be generated clearly from this pdf",
    )

    if any(blocked_text in summary.casefold() for blocked_text in blocked_fallbacks):
        return ""

    return summary


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


_LETTERHEAD_MARKERS = (
    "regd. office",
    "registered office",
    "central office",
    "corporate relationship dept",
    "kind attn",
    "cin:",
    "cin :",
    "website:",
    "phone:",
    "fax:",
)


def _unreadable_summary(summary: str) -> bool:
    text = " ".join((summary or "").split()).casefold()

    if not text:
        return True

    if "could not be read clearly enough" in text:
        return True

    if text == "no extractable text was found in this pdf.":
        return True

    if text.startswith(("reserve bank of india", "rbi/", "0 reserve bank of india")):
        if any(marker in text for marker in _LETTERHEAD_MARKERS):
            return True

        if len(text.split()) < 8:
            return True

        return False

    return False


def _looks_like_zip(filename: str, content: bytes) -> bool:
    return filename.lower().endswith(".zip") or content.startswith(b"PK\x03\x04")


def _looks_like_pdf(filename: str, content: bytes) -> bool:
    return filename.lower().endswith(".pdf") or content.startswith(b"%PDF")


def _filename_from_url(url: str) -> str:
    return Path(url.split("?", 1)[0]).name


def _pdf_filename(title: str) -> str:
    cleaned = " ".join(title.split()).strip() or "article"
    return f"{cleaned[:120]}.pdf"