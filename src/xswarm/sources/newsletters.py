from __future__ import annotations

import datetime as dt

import feedparser
import httpx

from ..config import settings
from .base import RawItem, parse_date

MAX_AGE_DAYS = 3


def fetch(client: httpx.Client | None = None) -> list[RawItem]:
    """Lab blogs and AI newsletters. These carry announcements that never hit arXiv."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=MAX_AGE_DAYS)
    items: list[RawItem] = []
    try:
        for url in settings.newsletter_feeds:
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            feed = feedparser.parse(response.text)
            source_name = (feed.feed.get("title") or url)[:64]
            for entry in feed.entries[:20]:
                published = parse_date(entry.get("published") or entry.get("updated"))
                if published and published < cutoff:
                    continue
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                items.append(
                    RawItem(
                        source="newsletter",
                        url=entry.get("link", ""),
                        title=title,
                        summary=(entry.get("summary") or "")[:2000],
                        published_at=published,
                        signals={"feed": source_name},
                    )
                )
    finally:
        if owns_client:
            client.close()
    return items
