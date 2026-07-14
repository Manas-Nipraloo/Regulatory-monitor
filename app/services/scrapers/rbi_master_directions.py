from datetime import date, datetime
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.site_registry import DiscoveredArticle, SiteConfig


RBI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.rbi.org.in/",
}
FEMA_MASTER_DIRECTIONS_URL = "https://rbi.org.in/scripts/BS_ViewMasterDirections.aspx?did=335"
UPDATED_DATE_PATTERN = re.compile(r"updated(?:\s+up\s+to|\s+as\s+on)?\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.I)


async def discover_rbi_fema_master_directions(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    category_url = site.url
    if "did=" not in category_url.casefold():
        category_url = FEMA_MASTER_DIRECTIONS_URL

    async with httpx.AsyncClient(timeout=60, headers=RBI_HEADERS, follow_redirects=True) as client:
        response = await client.get(category_url)
        response.raise_for_status()
        entries = _category_entries(response.text, category_url)

        articles: list[DiscoveredArticle] = []
        seen_ids: set[str] = set()
        for title, detail_url in entries:
            updated_date = _updated_date_from_text(title)
            if updated_date != run_date:
                continue

            detail_id = _detail_id(detail_url)
            if detail_id in seen_ids:
                continue
            seen_ids.add(detail_id)

            detail_response = await client.get(detail_url)
            detail_response.raise_for_status()
            pdf_url = _main_pdf_link(detail_response.text, detail_url)
            if not pdf_url:
                continue

            articles.append(
                DiscoveredArticle(
                    site=site,
                    title=_clean_title(title),
                    source_url=detail_url,
                    pdf_url=pdf_url,
                    published_date=updated_date,
                    filename=_filename_from_url(pdf_url) or f"{_clean_title(title)[:80]}.pdf",
                )
            )
    return articles


def _category_entries(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        title = " ".join(link.get_text(" ", strip=True).split())
        href = urljoin(base_url, link["href"])
        if not title or "bs_viewmasdirections.aspx" not in href.casefold():
            continue
        if "notificationuser.aspx" in href.casefold():
            continue
        entries.append((title, href))
    return entries


def _updated_date_from_text(value: str) -> date | None:
    match = UPDATED_DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date()
    except ValueError:
        return None


def _main_pdf_link(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        if ".pdf" in href.casefold() and "/rdocs/notification/pdfs/" in href.casefold():
            return href
    return ""


def _detail_id(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    return (query.get("id") or [url])[0]


def _filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def _clean_title(value: str) -> str:
    return " ".join(value.split()).strip()
