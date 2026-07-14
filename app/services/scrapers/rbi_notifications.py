from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.site_registry import DiscoveredArticle, SiteConfig


RBI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.rbi.org.in/",
}


async def discover_rbi_fema_notifications(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    async with httpx.AsyncClient(timeout=60, headers=RBI_HEADERS, follow_redirects=True) as client:
        response = await client.get(site.url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    articles: list[DiscoveredArticle] = []
    current_date: date | None = None

    for row in soup.find_all("tr"):
        row_text = " ".join(row.get_text(" ", strip=True).split())
        if not row_text:
            continue

        parsed_date = _parse_rbi_date(row_text)
        if parsed_date:
            current_date = parsed_date
            continue

        if current_date != run_date:
            continue

        title_link = _first_detail_link(row, site.url)
        pdf_link = _first_pdf_link(row, site.url)
        if not pdf_link:
            continue

        title = title_link[0] if title_link else _clean_title(row_text)
        source_url = title_link[1] if title_link else site.url
        articles.append(
            DiscoveredArticle(
                site=site,
                title=title,
                source_url=source_url,
                pdf_url=pdf_link,
                published_date=current_date,
                filename=_filename_from_url(pdf_link) or f"{_clean_title(title)[:80]}.pdf",
            )
        )

    return articles


def _parse_rbi_date(value: str) -> date | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _first_detail_link(row: object, base_url: str) -> tuple[str, str] | None:
    for link in row.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        title = " ".join(link.get_text(" ", strip=True).split())
        if title and "NotificationUser.aspx" in href:
            return title, href
    return None


def _first_pdf_link(row: object, base_url: str) -> str:
    for link in row.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        if ".pdf" in href.casefold():
            return href
    return ""


def _filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def _clean_title(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    for suffix in (" kb", " KB"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned
