import argparse
import asyncio
from datetime import date, datetime
import sys

import httpx

from app.config import get_settings
from app.services.document_downloader import download_document_bytes
from app.services.email_draft import build_daily_email_message, save_daily_email_draft
from app.services.google_workspace import google_workspace_enabled
from app.services.imap_draft import imap_credentials_ready, save_imap_draft
from app.services.site_registry import discover_current_date_articles, get_sites
from app.services.smtp_email import send_smtp_email, smtp_credentials_ready
from app.tasks.daily_monitor import DOWNLOAD_HEADERS, _process_downloaded_item


DEFAULT_SITE_FILTER = "NOC under Regulation 37 Updates"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_run_date(value: str) -> date:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("Use date format DD-MM-YYYY, YYYY-MM-DD, or DD/MM/YYYY.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Regulatory Monitor workflow step by step.")
    parser.add_argument("--date", type=parse_run_date, default=date.today(), help="Run date, e.g. 19-06-2026.")
    parser.add_argument("--site", default=DEFAULT_SITE_FILTER, help="Site name or remark filter.")
    parser.add_argument("--no-drive", action="store_true", help="Download and extract locally without uploading PDFs to Drive.")
    parser.add_argument("--save-webmail-draft", action="store_true", help="Save the draft into Hostinger Drafts.")
    parser.add_argument("--send-email", action="store_true", help="Send the email through Hostinger SMTP.")
    args = parser.parse_args()

    settings = get_settings()
    print("Regulatory Monitor CLI")
    print(f"Run date: {args.date.strftime('%d-%m-%Y')}")
    print(f"Site filter: {args.site}")
    print(f"Google Drive enabled: {google_workspace_enabled()}")
    print(f"Google Drive upload for this run: {not args.no_drive}")
    print(f"Groq enabled: {bool(settings.groq_api_key)}")
    print(f"Hostinger draft enabled for this run: {args.save_webmail_draft}")
    print(f"Hostinger send enabled for this run: {args.send_email}")
    print()

    sites = get_sites([args.site])
    print(f"Step 1 - Sites matched: {len(sites)}")
    for site in sites:
        print(f"  - {site.name} | {site.remark}")
    if not sites:
        print("No matching sites found. Stopping.")
        return
    print()

    articles = []
    async with httpx.AsyncClient(timeout=settings.download_timeout_seconds, follow_redirects=True) as client:
        for site in sites:
            print(f"Step 2 - Checking source rows for: {site.remark}")
            discovered = await discover_current_date_articles(args.date, site)
            print(f"  Matching rows/documents found for date: {len(discovered)}")
            if not discovered:
                continue

            for index, item in enumerate(discovered, start=1):
                print(f"  Document {index} of {len(discovered)}")
                print(f"    Title: {item.title}")
                print(f"    Published date: {item.published_date}")
                try:
                    print("    Downloading source file...")
                    content = await download_document_bytes(client, item, DOWNLOAD_HEADERS)
                    print(f"    Download complete. Size: {_format_bytes(len(content))}")

                    print("    Extracting PDFs from source file...")
                    print("    Groq is reading PDFs and generating heading/summary...")
                    if args.no_drive:
                        print("    Skipping Google Drive upload for local test...")
                    else:
                        print("    Uploading PDFs to Google Drive...")
                    processed = _process_downloaded_item(args.date, item, content, upload_drive=not args.no_drive)
                    print(f"    PDFs found and processed: {len(processed)}")
                    for pdf_index, article in enumerate(processed, start=1):
                        print(f"      PDF {pdf_index} of {len(processed)}")
                        print(f"        Heading generated: {article.title}")
                        print(f"        Summary generated: {article.summary}")
                        print(f"        Drive upload: {'done' if article.drive_pdf_url else 'skipped'}")
                    articles.extend(processed)
                except Exception as exc:
                    print(f"    Failed to process this document: {exc}")
    print()

    print(f"Step 5 - Total PDFs/articles ready for email: {len(articles)}")
    if not articles:
        print("No current-date articles found. No draft, Drive upload, or email send needed.")
        return

    draft_path = save_daily_email_draft(args.date, articles)
    print(f"Step 6 - Local HTML .eml draft saved: {draft_path}")

    message = build_daily_email_message(args.date, articles)
    if args.save_webmail_draft:
        print("Step 7 - Saving draft into Hostinger Drafts...")
        if not imap_credentials_ready(settings.email_credentials_file):
            print("  Hostinger IMAP credentials are not ready.")
        else:
            saved = save_imap_draft(message, settings.email_credentials_file)
            print(f"  Webmail draft saved: {saved}")
    else:
        print("Step 7 - Skipped Hostinger Drafts. Add --save-webmail-draft to enable it.")

    if args.send_email:
        print("Step 8 - Sending email through Hostinger SMTP...")
        if not smtp_credentials_ready(settings.email_credentials_file):
            print("  Hostinger SMTP credentials are not ready.")
        else:
            sent = send_smtp_email(message, settings.email_credentials_file)
            print(f"  Email sent: {sent}")
    else:
        print("Step 8 - Skipped sending email. Add --send-email to enable it.")

    print()
    print("Workflow complete.")


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{value} B"


if __name__ == "__main__":
    asyncio.run(main())
