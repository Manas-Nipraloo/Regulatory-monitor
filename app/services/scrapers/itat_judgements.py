from datetime import date, datetime
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.site_registry import DiscoveredArticle, SiteConfig


ITAT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://itatonline.org/",
}
MAX_PAGES_TO_SCAN = 20


async def discover_itat_judgements(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    articles: list[DiscoveredArticle] = []
    async with httpx.AsyncClient(timeout=60, headers=ITAT_HEADERS, follow_redirects=True) as client:
        for page_number in range(1, MAX_PAGES_TO_SCAN + 1):
            page_url = _page_url(site.url, page_number)
            response = await client.get(page_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            detail_links = _judgement_links(soup, page_url)
            if not detail_links:
                break

            page_dates: list[date] = []
            page_matched = False
            for title, detail_url in detail_links:
                detail_response = await client.get(detail_url)
                detail_response.raise_for_status()
                detail_soup = BeautifulSoup(detail_response.text, "html.parser")

                uploaded_date = _upload_date(detail_soup)
                if uploaded_date:
                    page_dates.append(uploaded_date)

                if uploaded_date != run_date:
                    continue

                pdf_url = _pdf_link(detail_soup, detail_url)
                if not pdf_url:
                    continue
                pdf_url = await _resolve_pdf_url(client, pdf_url)

                page_matched = True
                articles.append(
                    DiscoveredArticle(
                        site=site,
                        title=_clean_title(title),
                        source_url=detail_url,
                        pdf_url=pdf_url,
                        published_date=uploaded_date,
                        filename=_filename_from_url(pdf_url) or f"{_clean_title(title)[:100]}.pdf",
                    )
                )

            if page_dates and max(page_dates) < run_date:
                break
            if articles and page_dates and min(page_dates) < run_date and not page_matched:
                break

    return articles


def _page_url(base_url: str, page_number: int) -> str:
    if page_number == 1:
        return base_url
    return f"{base_url.rstrip('/')}/page/{page_number}/"


def _judgement_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for heading in soup.find_all(["h2", "h3", "h4"]):
        link = heading.find("a", href=True)
        if not link:
            continue
        href = urljoin(base_url, link["href"])
        if "/digest/verdicts/" not in href.casefold() or href in seen:
            continue
        title = _clean_title(link.get_text(" ", strip=True))
        if not title:
            continue
        seen.add(href)
        found.append((title, href))
    return found


def _upload_date(soup: BeautifulSoup) -> date | None:
    text = " ".join(soup.get_text(" ", strip=True).split())
    match = re.search(r"Date of upload:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", text, re.IGNORECASE)
    if not match:
        return None
    return _parse_date(match.group(1))


def _parse_date(value: str) -> date | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _pdf_link(soup: BeautifulSoup, base_url: str) -> str:
    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        text = link.get_text(" ", strip=True).casefold()
        if "pdf" in text or "pdf" in href.casefold():
            return href
    return ""


async def _resolve_pdf_url(client: httpx.AsyncClient, url: str) -> str:
    if ".pdf" in url.casefold():
        return url

    response = await client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").casefold()
    if "application/pdf" in content_type or response.content.startswith(b"%PDF"):
        return str(response.url)

    soup = BeautifulSoup(response.text, "html.parser")
    for link in soup.find_all("a", href=True):
        href = urljoin(str(response.url), link["href"])
        if ".pdf" in href.casefold():
            return href
    return str(response.url)


def _filename_from_url(url: str) -> str:
    name = Path(urlparse(url).path.rstrip("/")).name
    if not name or not name.lower().endswith(".pdf"):
        return ""
    return name


def _clean_title(value: str) -> str:
    return " ".join(value.replace("\ufffd", "-").split()).strip()
