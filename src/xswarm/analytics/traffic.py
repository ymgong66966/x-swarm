"""Pull page traffic for published articles.

Plausible is the configured provider because it exposes per-page and referrer
breakdowns behind one token. Rows are stored provider-agnostically, so swapping in GA4
later is a new fetch function, not a new table.
"""

from __future__ import annotations

import datetime as dt
import logging
import urllib.parse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import STREAM_CARE, Article, TrafficSnapshot

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0)


def configured() -> bool:
    return bool(settings.plausible_api_key and settings.plausible_site_id)


def _path(url: str) -> str:
    return urllib.parse.urlsplit(url).path or "/"


def _get(client: httpx.Client, endpoint: str, params: dict[str, str]) -> dict:
    response = client.get(
        f"{settings.plausible_base_url.rstrip('/')}/{endpoint}",
        params={"site_id": settings.plausible_site_id or "", **params},
        headers={"Authorization": f"Bearer {settings.plausible_api_key}"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def fetch_page(client: httpx.Client, path: str, period: str) -> dict[str, float]:
    payload = _get(
        client,
        "stats/aggregate",
        {
            "period": period,
            "metrics": "visitors,pageviews,bounce_rate,visit_duration",
            "filters": f"event:page=={path}",
        },
    )
    results = payload.get("results", {})
    if not isinstance(results, dict):
        return {}
    return {
        key: float(value.get("value", 0) or 0)
        for key, value in results.items()
        if isinstance(value, dict)
    }


def fetch_referrers(client: httpx.Client, path: str, period: str) -> dict[str, int]:
    payload = _get(
        client,
        "stats/breakdown",
        {
            "period": period,
            "property": "visit:source",
            "metrics": "visitors",
            "limit": "10",
            "filters": f"event:page=={path}",
        },
    )
    rows = payload.get("results", [])
    referrers: dict[str, int] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                referrers[str(row.get("source", "direct"))] = int(row.get("visitors", 0) or 0)
    return referrers


def collect(
    session: Session,
    *,
    days: int | None = None,
    client: httpx.Client | None = None,
) -> list[TrafficSnapshot]:
    """One snapshot per published article for the trailing window."""
    if not configured():
        log.info("traffic: no Plausible credentials, skipping")
        return []

    days = days or settings.dashboard_lookback_days
    period = f"{days}d"
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    articles = list(
        session.scalars(select(Article).where(Article.published_url.is_not(None)))
    )
    if not articles:
        return []

    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    snapshots: list[TrafficSnapshot] = []
    try:
        for article in articles:
            url = article.published_url or ""
            path = _path(url)
            try:
                metrics = fetch_page(client, path, period)
                referrers = fetch_referrers(client, path, period)
            except httpx.HTTPError as exc:  # one bad page must not lose the rest
                log.warning("traffic fetch failed for %s: %s", path, exc)
                continue
            existing = session.scalar(
                select(TrafficSnapshot).where(
                    TrafficSnapshot.url == url,
                    TrafficSnapshot.period_start == start,
                    TrafficSnapshot.period_end == end,
                )
            )
            snapshot = existing or TrafficSnapshot(
                url=url, stream=STREAM_CARE, period_start=start, period_end=end
            )
            snapshot.visitors = int(metrics.get("visitors", 0))
            snapshot.pageviews = int(metrics.get("pageviews", 0))
            snapshot.bounce_rate = metrics.get("bounce_rate", 0.0)
            snapshot.avg_seconds = metrics.get("visit_duration", 0.0)
            snapshot.referrers = referrers
            if existing is None:
                session.add(snapshot)
            snapshots.append(snapshot)
    finally:
        if owns_client:
            client.close()

    session.flush()
    log.info("traffic: stored %d snapshots", len(snapshots))
    return snapshots
