from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})")


@dataclass(slots=True)
class RawItem:
    """Source-agnostic ingestion record. Sources return these; the Scout persists them."""

    source: str
    url: str
    title: str
    summary: str = ""
    external_id: str | None = None
    authors: list[str] = field(default_factory=list)
    published_at: dt.datetime | None = None
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable identity across sources: the same arXiv paper found via arXiv, HF daily
        papers and Semantic Scholar must collapse to one row."""
        arxiv = _ARXIV_ID.search(self.external_id or "") or _ARXIV_ID.search(self.url)
        if arxiv:
            key = f"arxiv:{arxiv.group(1)}"
        else:
            key = f"title:{normalize_title(self.title)}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    from dateutil import parser

    try:
        parsed = parser.parse(value)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed
