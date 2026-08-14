"""Answer one question per URL: can a search or social crawler reach this, and does
what it finds describe the page correctly.

Deliberately dependency-free parsing — the checks are shallow (status, robots, sitemap
membership, canonical, title, description, noindex) and a regex reads them fine, which
keeps this runnable in CI without a headless browser.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import urllib.parse
import urllib.robotparser

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import STREAM_CARE, CrawlCheck

log = logging.getLogger(__name__)

USER_AGENT = "xswarm-crawl-check/1.0"
TIMEOUT = httpx.Timeout(20.0)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESC_RE = re.compile(
    r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']", re.IGNORECASE | re.DOTALL
)
CANONICAL_RE = re.compile(
    r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"'](.*?)[\"']", re.IGNORECASE
)
ROBOTS_META_RE = re.compile(
    r"<meta[^>]+name=[\"']robots[\"'][^>]+content=[\"'](.*?)[\"']", re.IGNORECASE
)
SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


def robots_reader(base: str, client: httpx.Client) -> urllib.robotparser.RobotFileParser | None:
    parser = urllib.robotparser.RobotFileParser()
    try:
        response = client.get(urllib.parse.urljoin(base, "/robots.txt"), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        log.warning("robots.txt unreachable for %s: %s", base, exc)
        return None
    if response.status_code >= 400:
        return None
    parser.parse(response.text.splitlines())
    return parser


def sitemap_urls(base: str, client: httpx.Client, *, depth: int = 1) -> set[str]:
    """Read /sitemap.xml, following one level of sitemap index."""
    try:
        response = client.get(urllib.parse.urljoin(base, "/sitemap.xml"), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        log.warning("sitemap unreachable for %s: %s", base, exc)
        return set()
    if response.status_code >= 400:
        return set()

    locations = {loc.strip() for loc in SITEMAP_LOC_RE.findall(response.text)}
    if "<sitemapindex" in response.text.lower() and depth > 0:
        nested: set[str] = set()
        for child in list(locations)[:10]:
            try:
                child_response = client.get(child, timeout=TIMEOUT)
            except httpx.HTTPError:
                continue
            if child_response.status_code < 400:
                nested |= {loc.strip() for loc in SITEMAP_LOC_RE.findall(child_response.text)}
        return nested or locations
    return locations


def inspect(html: str) -> dict[str, str]:
    title = TITLE_RE.search(html)
    description = META_DESC_RE.search(html)
    canonical = CANONICAL_RE.search(html)
    robots_meta = ROBOTS_META_RE.search(html)
    return {
        "title": _text(title.group(1)) if title else "",
        "meta_description": _text(description.group(1)) if description else "",
        "canonical": canonical.group(1).strip() if canonical else "",
        "robots_meta": (robots_meta.group(1) if robots_meta else "").lower(),
    }


def issues_for(
    url: str, status: int, allowed: bool, in_sitemap: bool, page: dict[str, str]
) -> list[str]:
    issues: list[str] = []
    if status >= 400:
        issues.append(f"HTTP {status}")
    if not allowed:
        issues.append("blocked by robots.txt")
    if "noindex" in page.get("robots_meta", ""):
        issues.append("noindex meta tag")
    if not in_sitemap:
        issues.append("not in sitemap.xml")
    title = page.get("title", "")
    if not title:
        issues.append("missing <title>")
    elif len(title) > 65:
        issues.append(f"title is {len(title)} chars (over 65)")
    description = page.get("meta_description", "")
    if not description:
        issues.append("missing meta description")
    elif len(description) > 160:
        issues.append(f"meta description is {len(description)} chars (over 160)")
    canonical = page.get("canonical", "")
    if canonical and canonical.rstrip("/") != url.rstrip("/"):
        issues.append(f"canonical points elsewhere: {canonical}")
    return issues


def check_url(
    url: str,
    client: httpx.Client,
    *,
    robots: urllib.robotparser.RobotFileParser | None,
    sitemap: set[str],
    stream: str = STREAM_CARE,
) -> CrawlCheck:
    allowed = robots.can_fetch(USER_AGENT, url) if robots else True
    started = dt.datetime.now(dt.timezone.utc)
    status = 0
    page: dict[str, str] = {}
    if not allowed:
        # Reporting that a page is blocked must not itself ignore the block.
        return CrawlCheck(
            url=url,
            stream=stream,
            status_code=status,
            robots_allowed=False,
            in_sitemap=any(entry.rstrip("/") == url.rstrip("/") for entry in sitemap),
            indexable=False,
            title="",
            meta_description="",
            canonical="",
            issues=["blocked by robots.txt"],
            response_ms=0.0,
        )
    try:
        response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        status = response.status_code
        if status < 400 and "html" in response.headers.get("content-type", ""):
            page = inspect(response.text)
    except httpx.HTTPError as exc:
        log.warning("crawl check failed for %s: %s", url, exc)

    elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000
    in_sitemap = any(entry.rstrip("/") == url.rstrip("/") for entry in sitemap)
    issues = issues_for(url, status, allowed, in_sitemap, page)
    return CrawlCheck(
        url=url,
        stream=stream,
        status_code=status,
        robots_allowed=allowed,
        in_sitemap=in_sitemap,
        indexable=status < 400 and allowed and "noindex" not in page.get("robots_meta", ""),
        title=page.get("title", ""),
        meta_description=page.get("meta_description", ""),
        canonical=page.get("canonical", ""),
        issues=issues,
        response_ms=elapsed,
    )


def run(
    session: Session,
    urls: list[str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[CrawlCheck]:
    base = settings.care_site_url
    urls = urls or [urllib.parse.urljoin(base, path) for path in settings.care_site_paths]
    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        robots = robots_reader(base, client)
        sitemap = sitemap_urls(base, client)
        checks = [
            check_url(url, client, robots=robots, sitemap=sitemap) for url in urls
        ]
    finally:
        if owns_client:
            client.close()

    for check in checks:
        session.add(check)
    session.flush()
    flagged = sum(1 for check in checks if check.issues)
    log.info("crawl: checked %d urls, %d with issues", len(checks), flagged)
    return checks
