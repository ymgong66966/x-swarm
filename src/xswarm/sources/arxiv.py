from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

import httpx

from ..config import settings
from .base import RawItem, parse_date

API = "https://export.arxiv.org/api/query"
RSS = "https://rss.arxiv.org/atom/{category}"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_ID_PREFIX = "oai:arXiv.org:"

logger = logging.getLogger(__name__)

# arXiv's terms of use: no more than one request every three seconds, single connection.
MIN_INTERVAL_S = 3.0
_last_call = 0.0

# export.arxiv.org throttles shared egress IPs hard (429 / hanging reads), so retry.
MAX_ATTEMPTS = 3
BACKOFF_S = 8.0
TIMEOUT_S = 60.0


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def fetch(client: httpx.Client | None = None) -> list[RawItem]:
    """Most recent submissions in the configured categories.

    arXiv only refreshes at midnight ET, so calling this more than once a day is wasted
    quota — the Scout is expected to run daily and cache.

    The query API rate-limits shared egress IPs aggressively; when it refuses us we fall
    back to the per-category announcement feeds, which carry the same day's submissions.
    """
    query = " OR ".join(f"cat:{c}" for c in settings.arxiv_categories)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": settings.arxiv_max_results,
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
    try:
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                time.sleep(BACKOFF_S * attempt)
            _throttle()
            try:
                response = client.get(API, params=params)
                response.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                continue
            return _parse(response.text)
        logger.warning("arXiv query API unavailable (%s), falling back to RSS", last_error)
        return _fetch_rss(client)
    finally:
        if owns_client:
            client.close()


def _fetch_rss(client: httpx.Client) -> list[RawItem]:
    items: list[RawItem] = []
    seen: set[str] = set()
    for category in settings.arxiv_categories:
        _throttle()
        response = client.get(RSS.format(category=category))
        response.raise_for_status()
        for item in _parse_rss(response.text):
            if item.external_id in seen:
                continue
            seen.add(item.external_id)
            items.append(item)
    return items[: settings.arxiv_max_results]


def _parse_rss(xml: str) -> list[RawItem]:
    root = ET.fromstring(xml)
    items: list[RawItem] = []
    for entry in root.findall("a:entry", NS):
        title = " ".join((entry.findtext("a:title", default="", namespaces=NS) or "").split())
        raw_id = (entry.findtext("a:id", default="", namespaces=NS) or "").strip()
        if not title or not raw_id.startswith(_ID_PREFIX):
            continue
        if entry.findtext("arxiv:announce_type", default="", namespaces=NS) != "new":
            continue
        arxiv_id = raw_id[len(_ID_PREFIX) :]
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=NS) or "").split())
        summary = summary.split("Abstract: ", 1)[-1]
        creators = entry.findtext("dc:creator", default="", namespaces=NS) or ""
        items.append(
            RawItem(
                source="arxiv",
                url=f"https://arxiv.org/abs/{arxiv_id.split('v')[0]}",
                external_id=arxiv_id,
                title=title,
                summary=summary,
                authors=[a.strip() for a in creators.split(",") if a.strip()],
                published_at=parse_date(entry.findtext("a:published", namespaces=NS)),
                signals={
                    "categories": [
                        c.attrib.get("term", "") for c in entry.findall("a:category", NS)
                    ]
                },
            )
        )
    return items


def _parse(xml: str) -> list[RawItem]:
    root = ET.fromstring(xml)
    items: list[RawItem] = []
    for entry in root.findall("a:entry", NS):
        entry_id = (entry.findtext("a:id", default="", namespaces=NS) or "").strip()
        title = " ".join((entry.findtext("a:title", default="", namespaces=NS) or "").split())
        if not title:
            continue
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=NS) or "").split())
        authors = [
            (a.findtext("a:name", default="", namespaces=NS) or "").strip()
            for a in entry.findall("a:author", NS)
        ]
        categories = [c.attrib.get("term", "") for c in entry.findall("a:category", NS)]
        items.append(
            RawItem(
                source="arxiv",
                url=entry_id,
                external_id=entry_id.rsplit("/", 1)[-1],
                title=title,
                summary=summary,
                authors=[a for a in authors if a],
                published_at=parse_date(entry.findtext("a:published", namespaces=NS)),
                signals={"categories": categories},
            )
        )
    return items
