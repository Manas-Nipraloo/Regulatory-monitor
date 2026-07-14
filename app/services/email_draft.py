from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path

from app.config import get_settings
from app.schemas import ArticleResult
from app.services.gmail_draft import load_email_credentials
from app.services.storage import email_draft_folder


def save_daily_email_draft(
    run_date: date,
    articles: list[ArticleResult],
    file_label: str | None = None,
    date_label: str | None = None,
) -> Path | None:
    if not articles:
        return None

    message = build_daily_email_message(run_date, articles, date_label=date_label)
    draft_name = file_label or run_date.strftime("%d-%m-%Y")
    draft_path = email_draft_folder(run_date) / f"{draft_name}.eml"
    draft_path.write_text(message.as_string(), encoding="utf-8")
    return draft_path


def build_daily_email_message(
    run_date: date,
    articles: list[ArticleResult],
    date_label: str | None = None,
) -> EmailMessage:
    settings = get_settings()
    credentials = load_email_credentials(settings.email_credentials_file)
    sender = credentials.get("sender_email") or settings.email_from
    to = _join_recipients(credentials.get("to")) or settings.email_to
    cc = _join_recipients(credentials.get("cc")) or settings.email_cc
    bcc = _join_recipients(credentials.get("bcc"))

    message = EmailMessage()
    message["Subject"] = _clean_subject(settings.email_subject)
    message["From"] = sender
    message["To"] = to
    message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    message.set_content(_draft_body(run_date, articles, date_label=date_label))
    message.add_alternative(_draft_html_body(run_date, articles, date_label=date_label), subtype="html")
    return message


def _draft_body(run_date: date, articles: list[ArticleResult], date_label: str | None = None) -> str:
    settings = get_settings()
    lines = [
        "Dear Team,",
        "",
        "Greetings to all.",
        "",
        "Kindly find the New Circulars and updated Drive link",
        "",
        f"Date : {date_label or run_date.strftime('%d-%m-%Y')}",
        "",
    ]

    date_groups = _group_by_effective_date(articles)
    if len(date_groups) <= 1:
        for remark_index, (remark, remark_articles) in enumerate(_group_by_remark(articles), start=1):
            lines.extend(_remark_section(remark_index, remark, remark_articles))
    else:
        for date_index, (group_date, date_articles) in enumerate(date_groups, start=1):
            lines.extend(
                [
                    f"{date_index}. Date : {group_date.strftime('%d-%m-%Y')}",
                    "",
                ]
            )
            for remark_index, (remark, remark_articles) in enumerate(_group_by_remark(date_articles), start=1):
                lines.extend(_remark_section(remark_index, remark, remark_articles, indent="   "))

    lines.extend(
        [
            "",
            "Best regards",
            "",
            settings.email_signature_name,
            settings.email_signature_title,
        ]
    )
    return "\n".join(lines)


def _group_by_effective_date(articles: list[ArticleResult]) -> list[tuple[date, list[ArticleResult]]]:
    grouped: dict[date, list[ArticleResult]] = {}
    for article in articles:
        if not _include_in_email(article):
            continue
        if article.published_date is None:
            continue
        grouped.setdefault(article.published_date, []).append(article)
    return list(grouped.items())


def _group_by_remark(articles: list[ArticleResult]) -> list[tuple[str, list[ArticleResult]]]:
    grouped: dict[str, list[ArticleResult]] = {}
    for article in articles:
        if not _include_in_email(article):
            continue
        grouped.setdefault(article.site_remark, []).append(article)
    return list(grouped.items())


def _remark_section(index: int, remark: str, articles: list[ArticleResult], indent: str = "") -> list[str]:
    drive_url = next(
        (
            article.drive_folder_url or article.drive_pdf_url
            for article in articles
            if article.drive_folder_url or article.drive_pdf_url
        ),
        "Pending Google Drive upload",
    )
    return [
        f"{indent}{index}. {remark}",
        "",
        f"{indent}URL : {drive_url}",
        "",
        f"{indent}Heading :",
        "",
        _numbered_text([article.title for article in articles], indent=indent),
        "",
        f"{indent}Summary :",
        "",
        _numbered_text([article.summary or "Summary pending." for article in articles], indent=indent),
        "",
    ]


def _draft_html_body(run_date: date, articles: list[ArticleResult], date_label: str | None = None) -> str:
    date_groups = _group_by_effective_date(articles)
    if len(date_groups) <= 1:
        sections = _html_remark_sections(articles)
    else:
        sections: list[str] = []
        for date_index, (group_date, date_articles) in enumerate(date_groups, start=1):
            sections.append(f"<p><strong>{date_index}. Date : {group_date.strftime('%d-%m-%Y')}</strong></p>")
            sections.extend(_html_remark_sections(date_articles, left_pad=20))

    settings = get_settings()
    return f"""<!doctype html>
<html>
  <body>
    <div style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; line-height: 1.35; color: #111;">
      <p>Dear Team,</p>
      <p>Greetings to all.</p>
      <p>Kindly find the New Circulars and updated Drive link</p>
      <p>Date : {escape(date_label or run_date.strftime('%d-%m-%Y'))}</p>
      {''.join(sections)}
      <p>Best regards,</p>
      <p>{escape(settings.email_signature_name)}<br>{escape(settings.email_signature_title)}</p>
    </div>
  </body>
</html>"""


def _html_remark_sections(articles: list[ArticleResult], left_pad: int = 0) -> list[str]:
    sections: list[str] = []
    for remark_index, (remark, remark_articles) in enumerate(_group_by_remark(articles), start=1):
        drive_url = next(
            (
                article.drive_folder_url or article.drive_pdf_url
                for article in remark_articles
                if article.drive_folder_url or article.drive_pdf_url
            ),
            "Pending Google Drive upload",
        )
        heading_items = "".join(
            f'<li style="margin-bottom: 12px;">{escape(_clean_summary_text(article.title))}</li>'
            for article in remark_articles
        )
        summary_items = "".join(
            f'<li style="margin-bottom: 12px;">{escape(_clean_summary_text(article.summary or "Summary pending."))}</li>'
            for article in remark_articles
        )
        url_html = (
            f'<a href="{escape(drive_url)}">{escape(drive_url)}</a>'
            if drive_url.startswith("http")
            else escape(drive_url)
        )
        margin_style = f"margin-left: {left_pad}px;" if left_pad else ""
        sections.append(
            f"""
            <div style="{margin_style}">
              <p>{remark_index}. {escape(remark)}</p>
              <p>URL : {url_html}</p>
              <p style="margin-bottom: 12px;">Heading :</p>
              <ol style="margin-top: 0; margin-bottom: 18px;">{heading_items}</ol>
              <p style="margin-bottom: 12px;">Summary :</p>
              <ol style="margin-top: 0; margin-bottom: 18px;">{summary_items}</ol>
            </div>
            """
        )
    return sections


def _numbered_text(values: list[str], indent: str = "") -> str:
    cleaned = [_clean_summary_text(value) for value in values if value and value.strip()]
    if not cleaned:
        return ""
    numbered = [f"{indent}{index}. {value}" for index, value in enumerate(cleaned, start=1)]
    return "\n\n".join(numbered)


def _clean_summary_text(value: str) -> str:
    words = " ".join(value.split()).split()
    if len(words) <= 55:
        return " ".join(words)
    return " ".join(words[:55]).rstrip(" .,;:")


def _include_in_email(article: ArticleResult) -> bool:
    text = f"{article.title} {article.summary or ''}"
    lowered = text.casefold()
    if "ieib" in lowered or "could not be read clearly enough" in lowered:
        return False
    if text.strip().startswith(("On B }", "]_&")):
        return False
    return True


def _join_recipients(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        return value.strip()
    return ""


def _clean_subject(value: str) -> str:
    subject = value.strip()
    if subject.casefold().startswith("subject"):
        _, separator, remainder = subject.partition(":")
        if separator:
            return remainder.strip()
    return subject
