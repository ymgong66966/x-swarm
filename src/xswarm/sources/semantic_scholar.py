from __future__ import annotations

import time

import httpx

from ..config import settings

API = "https://api.semanticscholar.org/graph/v1/paper/batch"
FIELDS = "title,citationCount,influentialCitationCount,venue,year,authors.hIndex"

# An API key grants 1 request/second; unauthenticated traffic shares one global pool.
MIN_INTERVAL_S = 1.05
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def enrich(arxiv_ids: list[str], client: httpx.Client | None = None) -> dict[str, dict]:
    """Citation counts and author h-index keyed by arXiv id.

    Used only as a credibility signal for the Curator; failures are non-fatal because
    brand-new papers legitimately have no Semantic Scholar record yet.
    """
    if not arxiv_ids:
        return {}
    headers = {}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    out: dict[str, dict] = {}
    try:
        for chunk_start in range(0, len(arxiv_ids), 100):
            chunk = arxiv_ids[chunk_start : chunk_start + 100]
            _throttle()
            response = client.post(
                API,
                params={"fields": FIELDS},
                headers=headers,
                json={"ids": [f"ARXIV:{i}" for i in chunk]},
            )
            if response.status_code != 200:
                continue
            for arxiv_id, record in zip(chunk, response.json(), strict=False):
                if not record:
                    continue
                authors = record.get("authors") or []
                out[arxiv_id] = {
                    "citations": record.get("citationCount", 0),
                    "influential_citations": record.get("influentialCitationCount", 0),
                    "venue": record.get("venue") or "",
                    "max_author_h_index": max(
                        (a.get("hIndex") or 0 for a in authors), default=0
                    ),
                }
    finally:
        if owns_client:
            client.close()
    return out
