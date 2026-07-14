from datetime import date
from pathlib import Path
from datetime import datetime

from pydantic import BaseModel, Field


class DailyRunRequest(BaseModel):
    run_date: date | None = None
    site_filters: list[str] = Field(default_factory=list)
    upload_drive: bool = True
    save_webmail_draft: bool = False
    send_email: bool = False


class ArticleResult(BaseModel):
    site_name: str
    site_remark: str
    title: str
    source_url: str
    published_date: date | None = None
    pdf_path: Path | None = None
    drive_pdf_url: str | None = None
    drive_folder_url: str | None = None
    metadata_path: Path | None = None
    summary: str | None = None


class DailyRunResponse(BaseModel):
    run_date: date
    articles_found: int
    articles: list[ArticleResult]
    draft_path: Path | None = None
    webmail_draft_saved: bool = False
    webmail_draft_error: str | None = None
    email_sent: bool = False
    email_error: str | None = None
    message: str


class RunHistoryArticle(BaseModel):
    site_remark: str
    title: str
    source_url: str
    published_date: date | None = None
    drive_pdf_url: str | None = None
    drive_folder_url: str | None = None
    summary: str | None = None


class RunHistoryEntry(BaseModel):
    id: str
    created_at: datetime
    run_date: date
    sources: list[str]
    articles_found: int
    upload_drive: bool
    save_webmail_draft: bool
    send_email: bool
    draft_path: Path | None = None
    webmail_draft_saved: bool = False
    webmail_draft_error: str | None = None
    email_sent: bool = False
    email_error: str | None = None
    articles: list[RunHistoryArticle] = Field(default_factory=list)
    status: str
    message: str


class PendingArticle(BaseModel):
    id: str
    discovered_at: datetime
    run_date: date
    site_name: str
    site_remark: str
    title: str
    source_url: str
    pdf_url: str
    filename: str | None = None
    published_date: date | None = None
    status: str = "pending"
    drive_pdf_url: str | None = None
    drive_folder_url: str | None = None
    summary: str | None = None
    decided_at: datetime | None = None


class PdfExtractionResponse(BaseModel):
    filename: str
    heading: str
    summary: str
    page_count: int
    extracted_text_chars: int
