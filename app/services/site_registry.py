from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from app.config import get_settings
from app.services.google_workspace import read_sheet_rows


@dataclass(frozen=True)
class SiteConfig:
    name: str
    remark: str
    url: str


@dataclass(frozen=True)
class DiscoveredArticle:
    site: SiteConfig
    title: str
    source_url: str
    pdf_url: str
    published_date: date | None = None
    filename: str | None = None


DEFAULT_SITES: list[SiteConfig] = [
    SiteConfig(
        name="BSE NOC under Regulation 37",
        remark="NOC under Regulation 37 Updates",
        url="https://www.bseindia.com/corporates/NOCUnder.aspx",
    ),
    SiteConfig(
        name="RBI FEMA Notifications",
        remark="FEMA Notifications",
        url="https://www.rbi.org.in/Scripts/NotificationUser.aspx",
    ),
    SiteConfig(
        name="RBI FEMA Master Directions",
        remark="FEMA Master Directions",
        url="https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?did=335",
    ),
    SiteConfig(
        name="RBI FEMA Master Circulars",
        remark="FEMA Master Circulars",
        url="https://rbi.org.in/scripts/FS_Notification.aspx?fn=5&fnn=2763",
    ),
    SiteConfig(
        name="RBI FEMA Press Release",
        remark="FEMA Press Release",
        url="https://www.rbi.org.in/scripts/FS_PressRelease.aspx?fn=5",
    ),
    SiteConfig(
        name="MCA Latest News and Important Updates",
        remark="MCA Latest News and Important Updates Link",
        url="https://www.mca.gov.in/content/mca/global/en/notifications-tender/news-updates/latest-news.html",
    ),
    SiteConfig(
        name="MCA Whats New",
        remark="MCA Whats New Tab Link",
        url="https://mca.gov.in/content/mca/global/en/notifications-tender/whats-new.html",
    ),
    SiteConfig(
        name="ITAT Bar Mumbai Judgements",
        remark="Website of ITAT Bar Mumbai",
        url="https://itatonline.org/digest/all-judgements/",
    ),
    SiteConfig(
        name="SEBI Regulations",
        remark="SEBI website (Regulations)",
        url="https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=3&smid=0",
    ),
    SiteConfig(
        name="SEBI Gazette Notifications",
        remark="SEBI website (Gazette Notifications)",
        url="https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=82&smid=0",
    ),
]


def get_sites(site_filters: list[str] | None = None) -> list[SiteConfig]:
    sites = _load_sites_from_sheet() or DEFAULT_SITES
    if not site_filters:
        return sites

    wanted = {item.casefold() for item in site_filters}
    return [
        site
        for site in sites
        if site.name.casefold() in wanted or site.remark.casefold() in wanted
    ]


async def discover_current_date_articles(run_date: date, site: SiteConfig) -> list[DiscoveredArticle]:
    if "sebi.gov.in" in site.url.casefold() and (
        "ssid=3" in site.url.casefold() or "ssid=82" in site.url.casefold()
    ):
        from app.services.scrapers.sebi_regulations import discover_sebi_regulations

        return await discover_sebi_regulations(run_date, site)
    if "itatonline.org" in site.url.casefold() and "all-judgements" in site.url.casefold():
        from app.services.scrapers.itat_judgements import discover_itat_judgements

        return await discover_itat_judgements(run_date, site)
    if "bseindia.com" in site.url.casefold() and "nocunder" in site.url.casefold():
        from app.services.scrapers.bse_noc_under import discover_bse_noc_under

        return await discover_bse_noc_under(run_date, site)
    if "rbi.org.in" in site.url.casefold() and "notificationuser" in site.url.casefold():
        from app.services.scrapers.rbi_notifications import discover_rbi_fema_notifications

        return await discover_rbi_fema_notifications(run_date, site)
    if "rbi.org.in" in site.url.casefold() and "viewmasterdirections" in site.url.casefold():
        from app.services.scrapers.rbi_master_directions import discover_rbi_fema_master_directions

        return await discover_rbi_fema_master_directions(run_date, site)
    if "rbi.org.in" in site.url.casefold() and "fs_notification" in site.url.casefold():
        from app.services.scrapers.rbi_master_circulars import discover_rbi_fema_master_circulars

        return await discover_rbi_fema_master_circulars(run_date, site)
    if "rbi.org.in" in site.url.casefold() and "fs_pressrelease" in site.url.casefold():
        from app.services.scrapers.rbi_press_release import discover_rbi_fema_press_release

        return await discover_rbi_fema_press_release(run_date, site)
    if "mca.gov.in" in site.url.casefold() and (
        "latest-news" in site.url.casefold() or "whats-new" in site.url.casefold()
    ):
        from app.services.scrapers.mca_latest_news import discover_mca_latest_news

        return await discover_mca_latest_news(run_date, site)
    return []


async def discover_all_current_articles(site: SiteConfig) -> list[DiscoveredArticle] | None:
    """Return every currently-listed article (unfiltered by date), or None if the source
    does not support a full listing. Used by 'new arrivals' detection."""
    if "bseindia.com" in site.url.casefold() and "nocunder" in site.url.casefold():
        from app.services.scrapers.bse_noc_under import all_bse_noc_articles

        return await all_bse_noc_articles(site)
    return None


def _load_sites_from_sheet() -> list[SiteConfig]:
    settings = get_settings()
    if not settings.google_sheet_url:
        return []

    rows = read_sheet_rows(settings.google_sheet_url, settings.sheet_name)
    sites: list[SiteConfig] = []
    for row in rows:
        url = _pick_value(row, "Link", "URL", "Site Link", "Website")
        if not url:
            continue

        remark = _pick_value(row, "Remarks", "Remark", "Site Remark") or _host_label(url)
        name = _pick_value(row, "Site", "Site Name", "Name") or remark
        sites.append(SiteConfig(name=name, remark=remark, url=url))

    return sites


def _pick_value(row: dict[str, str], *names: str) -> str:
    normalized = {key.strip().casefold(): value.strip() for key, value in row.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if value:
            return value
    return ""


def _host_label(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url
