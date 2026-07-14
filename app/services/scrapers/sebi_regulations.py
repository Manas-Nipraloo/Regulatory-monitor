from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.site_registry import DiscoveredArticle, SiteConfig


SEBI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.sebi.gov.in/",
}


async def discover_sebi_regulations(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    articles: list[DiscoveredArticle] = []
    async with httpx.AsyncClient(timeout=60, headers=SEBI_HEADERS, follow_redirects=True, trust_env=False) as client:
        response = await client.get(site.url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for title, detail_url in _listing_links(soup, site.url):
            if f"/{run_date.strftime('%b').casefold()}-{run_date.year}/" not in detail_url.casefold():
                continue

            detail_response = await client.get(detail_url)
            detail_response.raise_for_status()
            detail_soup = BeautifulSoup(detail_response.text, "html.parser")

            published_date = _detail_date(detail_soup)
            if published_date != run_date:
                continue

            pdf_url = _iframe_pdf_url(detail_soup, detail_url)
            if not pdf_url:
                continue

            articles.append(
                DiscoveredArticle(
                    site=site,
                    title=title,
                    source_url=detail_url,
                    pdf_url=pdf_url,
                    published_date=published_date,
                    filename=_filename_from_url(pdf_url) or f"{_clean_title(title)[:100]}.pdf",
                )
            )

    return articles


def _listing_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    category_path = _category_path(base_url)
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        title = _clean_title(link.get_text(" ", strip=True))
        if not title or category_path not in href.casefold() or href in seen:
            continue
        seen.add(href)
        links.append((title, href))
    return links


def _category_path(base_url: str) -> str:
    if "ssid=82" in base_url.casefold():
        return "/legal/gazette-notification/"
    return "/legal/regulations/"


def _detail_date(soup: BeautifulSoup) -> date | None:
    date_node = soup.select_one(".date_value h5")
    if date_node:
        parsed = _parse_date(date_node.get_text(" ", strip=True))
        if parsed:
            return parsed

    text = " ".join(soup.get_text(" ", strip=True).split())
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        for index in range(max(len(text) - 12, 0)):
            candidate = text[index : index + 12]
            parsed = _parse_date(candidate, fmt)
            if parsed:
                return parsed
    return None


def _parse_date(value: str, fmt: str | None = None) -> date | None:
    formats = [fmt] if fmt else ["%b %d, %Y", "%B %d, %Y"]
    for date_format in formats:
        if not date_format:
            continue
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            pass
    return None


def _iframe_pdf_url(soup: BeautifulSoup, base_url: str) -> str:
    for tag in soup.find_all(["iframe", "embed", "object"]):
        raw = tag.get("src") or tag.get("data") or ""
        if not raw:
            continue
        src = urljoin(base_url, raw)
        parsed = urlparse(src)
        file_values = parse_qs(parsed.query).get("file", [])
        if file_values and ".pdf" in file_values[0].casefold():
            return file_values[0]
        if ".pdf" in src.casefold():
            return src
    return ""


def _filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def _clean_title(value: str) -> str:
    return " ".join(value.replace("\ufffd", "-").split()).strip()
