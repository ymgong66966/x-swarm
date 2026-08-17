from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Draft, PostMetric, Publication
from ..publishers import TypefullyClient, configured_social_sets

log = logging.getLogger(__name__)

_FIELD_MAP = {
    "impressions": ("impressions",),
    "likes": ("likes",),
    "replies": ("comments", "replies"),
    "reposts": ("shares", "reposts", "retweets"),
    "quotes": ("quotes",),
    "bookmarks": ("saves", "bookmarks"),
    "link_clicks": ("link_clicks",),
    "profile_clicks": ("profile_clicks",),
}


def _metric_values(row: dict) -> dict[str, int]:
    """Typefully omits metrics X did not return, and has renamed a few over time."""
    values = {}
    for field, keys in _FIELD_MAP.items():
        for key in keys:
            if row.get(key) is not None:
                values[field] = int(row[key])
                break
        else:
            values[field] = 0
    return values


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()[:60]


def match_publication(row: dict, publications: list[Publication]) -> Publication | None:
    """Prefer Typefully's own draft id; fall back to the post preview text, which is
    how posts created outside this pipeline still get attributed."""
    draft_id = row.get("draft_id")
    if draft_id is not None:
        by_id = {p.provider_draft_id: p for p in publications if p.provider_draft_id}
        hit = by_id.get(str(draft_id))
        if hit:
            return hit
    post_id = row.get("post_id")
    if post_id:
        by_post = {p.post_id: p for p in publications if p.post_id}
        hit = by_post.get(str(post_id))
        if hit:
            return hit
    preview = _normalize(row.get("preview_text") or "")
    if not preview:
        return None
    for publication in publications:
        if _normalize(publication.draft.body).startswith(preview[:40]):
            return publication
    return None


def ingest(session: Session, rows: list[dict], *, captured_at: dt.datetime | None = None) -> int:
    captured_at = captured_at or dt.datetime.now(dt.timezone.utc)
    publications = list(session.scalars(select(Publication).join(Draft)))
    stored = 0
    for row in rows:
        publication = match_publication(row, publications)
        if publication is None:
            log.debug("unmatched analytics row %s", row.get("post_id"))
            continue
        if row.get("post_id"):
            publication.post_id = str(row["post_id"])
        if row.get("url"):
            publication.post_url = row["url"]
        if publication.status != "published":
            publication.status = "published"
            publication.published_at = publication.published_at or captured_at
        session.add(
            PostMetric(
                publication=publication, captured_at=captured_at, **_metric_values(row)
            )
        )
        stored += 1
    session.flush()
    log.info("stored %d/%d analytics rows", stored, len(rows))
    return stored


def run(
    session: Session,
    *,
    days: int | None = None,
    client: TypefullyClient | None = None,
) -> int:
    days = days or settings.metrics_lookback_days
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    if client is not None:
        return ingest(session, client.analytics_posts(start, end))
    # Posts live on two X accounts now, and each social set reports only its own.
    clients = [TypefullyClient(social_set_id=s) for s in configured_social_sets()]
    return sum(ingest(session, c.analytics_posts(start, end)) for c in clients)
