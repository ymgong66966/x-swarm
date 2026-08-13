from __future__ import annotations

import datetime as dt

import httpx

from ..config import settings
from .base import RawItem, parse_date

API = "https://huggingface.co/api/daily_papers"


def fetch(day: dt.date | None = None, client: httpx.Client | None = None) -> list[RawItem]:
    """Hugging Face Daily Papers — community-curated, so upvotes are a free proxy for
    'the ML crowd already thinks this matters'. Highest signal-per-request source we have."""
    params = {
        "date": (day or dt.date.today()).isoformat(),
        "limit": 50,
        "sort": "trending",
    }
    headers = {"Authorization": f"Bearer {settings.hf_token}"} if settings.hf_token else {}
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.get(API, params=params, headers=headers)
        response.raise_for_status()
        return _parse(response.json())
    finally:
        if owns_client:
            client.close()


def _parse(payload: list[dict]) -> list[RawItem]:
    items: list[RawItem] = []
    for entry in payload:
        paper = entry.get("paper") or {}
        arxiv_id = paper.get("id")
        title = (paper.get("title") or entry.get("title") or "").strip()
        if not title:
            continue
        items.append(
            RawItem(
                source="hf_daily",
                url=f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else "",
                external_id=arxiv_id,
                title=title,
                summary=(paper.get("summary") or "").strip(),
                authors=[a.get("name", "") for a in paper.get("authors", []) if a.get("name")],
                published_at=parse_date(paper.get("publishedAt") or entry.get("publishedAt")),
                signals={
                    "hf_upvotes": paper.get("upvotes", 0),
                    "hf_comments": entry.get("numComments", 0),
                },
            )
        )
    return items
