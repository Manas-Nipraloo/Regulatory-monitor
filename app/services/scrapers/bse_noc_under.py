from datetime import date, datetime
from urllib.parse import quote

import httpx

from app.services.site_registry import DiscoveredArticle, SiteConfig


BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/corporates/NOCUnder_New",
}


async def discover_bse_noc_under(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    """Date-based discovery: only rows whose 'Date of Uploading' equals run_date."""
    articles = await all_bse_noc_articles(site)
    return [article for article in articles if article.published_date == run_date]


async def all_bse_noc_articles(site: SiteConfig) -> list[DiscoveredArticle]:
    """Every document currently listed on the BSE NOC page, regardless of date.

    Used by the 'new arrivals' detector so rows that appear late with an old date are caught.
    """
    api_url = (
        "https://api.bseindia.com/BseIndiaAPI/api/GetNocUnder_ng/w"
        "?flag=2&ID=&exchId=&Company_Name=&dt_tm="
    )
    async with httpx.AsyncClient(timeout=60, headers=BSE_HEADERS, follow_redirects=True, trust_env=False) as client:
        response = await client.get(api_url)
        response.raise_for_status()
        rows = response.json().get("Table", [])

    articles: list[DiscoveredArticle] = []
    for row in rows:
        published_date = _parse_bse_date(row.get("DATE", ""))

        for field_name, label in [
            ("Draft_Scheme", "Draft Scheme / NOC Document"),
            ("Complain_Report", "Complaint Report"),
            ("Observation_Letter", "Observation Letter of Exchange"),
        ]:
            filename = str(row.get(field_name) or "").strip()
            if not filename or filename == "---":
                continue

            company = str(row.get("company_name") or "BSE NOC").strip()
            remarks = str(row.get("Remarks") or "").strip()
            title = f"{company} - {label}"
            if remarks:
                title = f"{title} - {remarks}"
            download_url = "https://www.bseindia.com/Download/NocUnder/" + quote(filename)
            articles.append(
                DiscoveredArticle(
                    site=site,
                    title=title,
                    source_url=site.url,
                    pdf_url=download_url,
                    published_date=published_date,
                    filename=filename,
                )
            )
    return articles


def _parse_bse_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None
