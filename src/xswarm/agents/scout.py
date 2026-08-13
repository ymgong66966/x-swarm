from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Item
from ..sources import RawItem, arxiv, github, hf_papers, newsletters, semantic_scholar

log = logging.getLogger(__name__)

SOURCES = {
    "arxiv": arxiv.fetch,
    "hf_daily": hf_papers.fetch,
    "newsletters": newsletters.fetch,
    "github": github.fetch,
}


def collect(only: list[str] | None = None) -> list[RawItem]:
    """Fetch from every source. One source failing must never lose the others' results."""
    raw: list[RawItem] = []
    for name, fetcher in SOURCES.items():
        if only and name not in only:
            continue
        try:
            found = fetcher()
        except Exception:
            log.exception("source %s failed", name)
            continue
        log.info("source %s returned %d items", name, len(found))
        raw.extend(found)
    return _enrich(_merge(raw))


def _merge(raw: list[RawItem]) -> list[RawItem]:
    """Collapse the same paper seen through several sources, keeping the richest record
    and unioning the signals (an arXiv entry that HF also surfaced keeps its upvotes)."""
    merged: dict[str, RawItem] = {}
    for item in raw:
        existing = merged.get(item.fingerprint)
        if existing is None:
            merged[item.fingerprint] = item
            continue
        existing.signals.update(item.signals)
        if len(item.summary) > len(existing.summary):
            existing.summary = item.summary
        if not existing.authors:
            existing.authors = item.authors
        if not existing.published_at:
            existing.published_at = item.published_at
        existing.signals.setdefault("also_seen_in", []).append(item.source)
    return list(merged.values())


def _enrich(items: list[RawItem]) -> list[RawItem]:
    arxiv_ids = [
        i.external_id for i in items if i.source in ("arxiv", "hf_daily") and i.external_id
    ]
    cleaned = [i.split("v")[0] for i in arxiv_ids]
    try:
        enrichment = semantic_scholar.enrich(cleaned)
    except Exception:
        log.exception("semantic scholar enrichment failed")
        return items
    for item in items:
        if not item.external_id:
            continue
        record = enrichment.get(item.external_id.split("v")[0])
        if record:
            item.signals.update(record)
    return items


def persist(session: Session, raw: list[RawItem]) -> list[Item]:
    """Insert new items; refresh signals on ones we have already seen."""
    fingerprints = [i.fingerprint for i in raw]
    known = {
        item.fingerprint: item
        for item in session.scalars(select(Item).where(Item.fingerprint.in_(fingerprints)))
    }
    stored: list[Item] = []
    for candidate in raw:
        item = known.get(candidate.fingerprint)
        if item is None:
            item = Item(
                fingerprint=candidate.fingerprint,
                source=candidate.source,
                external_id=candidate.external_id,
                url=candidate.url,
                title=candidate.title,
                summary=candidate.summary,
                authors=candidate.authors,
                published_at=candidate.published_at,
                signals=candidate.signals,
            )
            session.add(item)
            stored.append(item)
        else:
            item.signals = {**item.signals, **candidate.signals}
    session.flush()
    return stored


def run(session: Session, only: list[str] | None = None) -> list[Item]:
    return persist(session, collect(only=only))
