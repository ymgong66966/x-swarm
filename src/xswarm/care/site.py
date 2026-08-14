"""Read the company website and store what it actually says.

Product claims in articles must trace back to a row here, so the site stays the single
source of truth and nobody has to remember to update a prompt when marketing changes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.parse
import urllib.robotparser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SiteFact
from .sources import USER_AGENT

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK = re.compile(
    r"<(h1|h2|h3|h4|p|li)\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE
)
_WS = re.compile(r"\s+")

# Which audience a page speaks to. Anything else is shared context.
PAGE_AUDIENCE = {"/providers": "provider", "/become-a-trainer": "clinician"}

MIN_FACT_CHARS = 25
MAX_FACT_CHARS = 600


def robots_allows(url: str, client: httpx.Client) -> bool:
    """We publish for this company, and we still ask its robots.txt first."""
    parts = urllib.parse.urlsplit(url)
    try:
        response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    except httpx.HTTPError:
        return True  # unreachable robots.txt is not a disallow
    if response.status_code != 200 or not response.text.strip():
        return True
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def extract(html: str, url: str) -> list[tuple[str, str]]:
    """(section heading, statement) pairs, in document order."""
    body = _SCRIPT.sub(" ", html)
    section = ""
    facts: list[tuple[str, str]] = []
    for match in _BLOCK.finditer(body):
        tag = match.group(1).lower()
        text = _WS.sub(" ", _TAG.sub(" ", match.group(2))).strip()
        if not text:
            continue
        if tag in ("h1", "h2", "h3", "h4"):
            section = text[:120]
            continue
        if not MIN_FACT_CHARS <= len(text) <= MAX_FACT_CHARS:
            continue
        facts.append((section, text))
    log.info("extracted %d facts from %s", len(facts), url)
    return facts


def fingerprint(url: str, text: str) -> str:
    return hashlib.sha256(f"{url}|{text}".encode()).hexdigest()[:32]


def sync(session: Session, client: httpx.Client | None = None) -> list[SiteFact]:
    """Refresh the product-fact table from the live site. Idempotent."""
    owns = client is None
    client = client or httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    stored: list[SiteFact] = []
    try:
        for path in settings.care_site_paths:
            url = urllib.parse.urljoin(settings.care_site_url, path)
            if not robots_allows(url, client):
                log.warning("robots.txt disallows %s; skipping", url)
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("could not read %s", url)
                continue
            audience = PAGE_AUDIENCE.get(path, "all")
            for section, text in extract(response.text, url):
                digest = fingerprint(url, text)
                existing = session.scalar(
                    select(SiteFact).where(SiteFact.fingerprint == digest)
                )
                if existing is not None:
                    existing.section = section
                    existing.audience = audience
                    continue
                fact = SiteFact(
                    fingerprint=digest,
                    url=url,
                    section=section,
                    text=text,
                    audience=audience,
                )
                session.add(fact)
                stored.append(fact)
        session.flush()
    finally:
        if owns:
            client.close()
    log.info("stored %d new site facts", len(stored))
    return stored


def context(session: Session, audience: str, limit: int = 40) -> list[SiteFact]:
    """Product facts the writer may use, audience-first."""
    facts = list(
        session.scalars(
            select(SiteFact).where(SiteFact.audience.in_([audience, "all"])).limit(limit * 2)
        )
    )
    facts.sort(key=lambda f: 0 if f.audience == audience else 1)
    return facts[:limit]
