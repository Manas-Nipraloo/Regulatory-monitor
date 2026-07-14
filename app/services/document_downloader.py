from pathlib import Path
import asyncio

import httpx
from app.services.site_registry import DiscoveredArticle

CHROME_PATHS = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
]
MCA_HOME_URL = "https://www.mca.gov.in/content/mca/global/en/home.html"


async def download_document_bytes(
    client: httpx.AsyncClient,
    item: DiscoveredArticle,
    headers: dict[str, str],
) -> bytes:
    if "mca.gov.in" in item.pdf_url.casefold():
        return await _download_mca_document(item.pdf_url)

    for attempt in range(1, 4):
        try:
            response = await client.get(item.pdf_url, headers=headers)
            response.raise_for_status()
            return response.content
        except (httpx.HTTPError, httpx.TimeoutException):
            if attempt == 3:
                raise
            await asyncio.sleep(attempt)

    raise RuntimeError("Document download failed after retries.")


async def _download_mca_document(url: str) -> bytes:
    async_playwright = _load_async_playwright()
    url = _normalize_mca_url(url)
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
        await page.goto(MCA_HOME_URL, wait_until="networkidle", timeout=60000)
        content = await page.evaluate(
            """async (url) => {
                const response = await fetch(url, { credentials: 'include' });
                if (!response.ok) {
                    throw new Error(`MCA download failed with status ${response.status}`);
                }
                const buffer = await response.arrayBuffer();
                return Array.from(new Uint8Array(buffer));
            }""",
            url,
        )
        await browser.close()

    data = bytes(content)
    if not data.startswith(b"%PDF"):
        raise RuntimeError("MCA download did not return a PDF document.")
    return data


def _normalize_mca_url(url: str) -> str:
    if url.startswith("https://mca.gov.in/"):
        return url.replace("https://mca.gov.in/", "https://www.mca.gov.in/", 1)
    if url.startswith("http://mca.gov.in/"):
        return url.replace("http://mca.gov.in/", "https://www.mca.gov.in/", 1)
    return url


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
            "MCA documents require a working Playwright install."
        ) from exc
    return async_playwright
