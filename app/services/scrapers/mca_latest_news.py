import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from app.services.site_registry import DiscoveredArticle, SiteConfig


CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
]
MCA_HOME_URL = "https://www.mca.gov.in/content/mca/global/en/home.html"
TABMESSAGES_FRAGMENT = "/bin/dms/tabmessages"


async def discover_mca_latest_news(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    rows = await _latest_news_rows(site.url)
    articles: list[DiscoveredArticle] = []

    for row in rows:
        published_date = _parse_mca_date(str(row.get("column2") or ""))
        if published_date != run_date:
            continue

        doc_id = str(row.get("docID") or "").strip()
        href = str(row.get("column3") or "").strip()
        if doc_id and not href:
            href = f"/bin/dms/getdocument?mds={quote(doc_id, safe='')}&type=download"
        if not href:
            continue

        title = _clean_text(str(row.get("column1") or ""))
        pdf_url = urljoin(site.url, href)
        articles.append(
            DiscoveredArticle(
                site=site,
                title=title,
                source_url=site.url,
                pdf_url=pdf_url,
                published_date=published_date,
                filename=_filename_from_url(pdf_url) or f"{title[:80]}.pdf",
            )
        )

    return articles


async def _latest_news_rows(url: str) -> list[dict[str, object]]:
    async_playwright = _load_async_playwright()
    endpoint_bodies: list[str] = []
    table_rows: list[dict[str, object]] = []
    direct_rows: list[dict[str, object]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=_browser_executable_path(),
            headless=True,
            args=["--disable-extensions", "--disable-pdf-extension"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        async def handle_response(response) -> None:
            if TABMESSAGES_FRAGMENT not in response.url:
                return
            try:
                endpoint_bodies.append(await response.text())
            except Exception:
                return

        page.on("response", lambda response: asyncio.create_task(handle_response(response)))
        await page.goto(MCA_HOME_URL, wait_until="networkidle", timeout=60000)
        if "whats-new" in url.casefold():
            direct_rows = await _search_doc_list_rows(page, folder="325")
            await browser.close()
            return direct_rows
        endpoint_bodies.clear()
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)
        table_rows = await _table_rows_from_page(page, url)
        await browser.close()

    if direct_rows:
        return direct_rows
    for body in endpoint_bodies:
        rows = _rows_from_endpoint_body(body)
        if rows:
            return rows
    return table_rows


async def _search_doc_list_rows(page, folder: str) -> list[dict[str, object]]:
    payload = await page.evaluate(
        """async (folder) => {
            const dialog = encodeURIComponent(JSON.stringify({
                folder,
                language: 'English',
                totalColumns: 2,
                columns: ['Title', 'Date']
            }));
            const url = `/bin/dms/searchDocList?page=1&perPage=100&sortField=Date&sortOrder=D&searchField=Title&searchKeyword=&startDate=&endDate=&filter=&dialog=${dialog}`;
            const response = await fetch(url, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`MCA searchDocList failed with status ${response.status}`);
            }
            return await response.json();
        }""",
        folder,
    )
    details = payload.get("documentDetails") if isinstance(payload, dict) else None
    if not details:
        return []
    rows = json.loads(details) if isinstance(details, str) else details
    return rows if isinstance(rows, list) else []


async def _table_rows_from_page(page, base_url: str) -> list[dict[str, object]]:
    rows = await page.locator("table tbody tr, table tr").evaluate_all(
        """(rows) => rows.map((row) => {
            const cells = Array.from(row.querySelectorAll('td'));
            if (cells.length < 2) return null;
            const anchors = Array.from(row.querySelectorAll('a[href]'));
            const pdfAnchor = anchors.find((a) => /\\.pdf(\\?|$)/i.test(a.getAttribute('href') || '')) || anchors[0];
            const fileCell = cells[0];
            const title = (pdfAnchor ? pdfAnchor.innerText : fileCell.innerText)
                .replace(/\\s+/g, ' ')
                .trim();
            const href = pdfAnchor ? pdfAnchor.getAttribute('href') : '';
            const dateText = cells.length >= 3 ? cells[cells.length - 2].innerText : cells[1].innerText;
            return {
                column1: title,
                column2: dateText.replace(/\\s+/g, '').trim(),
                column3: href,
                docType: href && /\\.pdf(\\?|$)/i.test(href) ? 'PDF' : 'LINK'
            };
        }).filter(Boolean)"""
    )
    normalized: list[dict[str, object]] = []
    for row in rows:
        href = str(row.get("column3") or "").strip()
        if not href:
            continue
        normalized.append(
            {
                **row,
                "column2": _normalize_table_date(str(row.get("column2") or "")),
                "column3": urljoin(base_url, href),
            }
        )
    return normalized


def _rows_from_endpoint_body(body: str) -> list[dict[str, object]]:
    if not body.lstrip().startswith("{"):
        return []
    data = json.loads(body)
    details = data.get("documentDetails")
    if not details:
        return []
    rows = json.loads(details) if isinstance(details, str) else details
    return rows if isinstance(rows, list) else []


def _parse_mca_date(value: str) -> date | None:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _normalize_table_date(value: str) -> str:
    compact = "".join(value.split())
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(compact, fmt).strftime("%d-%m-%Y")
        except ValueError:
            pass
    return compact


def _clean_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def _filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def _browser_executable_path() -> str | None:
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    return None


def _load_async_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is unavailable in this Python environment. "
            "MCA discovery requires a working Playwright install."
        ) from exc
    return async_playwright
