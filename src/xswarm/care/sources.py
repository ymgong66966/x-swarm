"""Where care-stream material comes from.

Every fetcher returns `RawItem`s carrying two extra signals the rest of the stream
depends on:

* `evidence_kind` — "regulatory", "research", "press" or "signal". Only the first two
  can support a factual claim; "signal" (Reddit, LinkedIn chatter) may motivate a piece
  and be described as sentiment, never cited as fact.
* `audience_hint` — provider / clinician / caregiver, used by the curator.

Only public feeds and documented APIs are used. Nothing behind a login is touched.
"""

from __future__ import annotations

import datetime as dt
import logging
import urllib.parse

import feedparser
import httpx

from ..config import settings
from ..sources.base import RawItem, parse_date

log = logging.getLogger(__name__)

USER_AGENT = "xswarm-care-bot/1.0 (+https://alvernahealth.com)"

FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"
PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

# Federal Register publishes everything CMS does; only these are about our subject.
FR_TERMS = ("caregiver", "telehealth", "home health", "physician fee schedule")

CAREGIVER_KEYWORDS = (
    "caregiver",
    "care giver",
    "family caregiver",
    "dementia",
    "home health",
    "telehealth",
    "discharge",
    "readmission",
    "aging",
    "medicare",
)


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ), True


def _cutoff() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=settings.care_source_max_age_days)


def federal_register(client: httpx.Client | None = None) -> list[RawItem]:
    """CMS rules and notices. The only place a coverage or payment rule is authoritative."""
    http, owns = _client(client)
    items: list[RawItem] = []
    try:
        for term in FR_TERMS:
            params = {
                "conditions[agencies][]": "centers-for-medicare-medicaid-services",
                "conditions[term]": term,
                "conditions[publication_date][gte]": _cutoff().date().isoformat(),
                "order": "newest",
                "per_page": "10",
                "fields[]": [
                    "title",
                    "abstract",
                    "html_url",
                    "publication_date",
                    "document_number",
                    "type",
                    "agencies",
                ],
            }
            try:
                response = http.get(FEDERAL_REGISTER_API, params=params)
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("federal register query %r failed", term)
                continue
            for doc in response.json().get("results", []):
                title = (doc.get("title") or "").strip()
                if not title:
                    continue
                items.append(
                    RawItem(
                        source="federal_register",
                        url=doc.get("html_url", ""),
                        title=title,
                        summary=(doc.get("abstract") or "")[:4000],
                        external_id=doc.get("document_number"),
                        published_at=parse_date(doc.get("publication_date")),
                        signals={
                            "evidence_kind": "regulatory",
                            "audience_hint": "provider",
                            "document_type": doc.get("type", ""),
                            "matched_term": term,
                        },
                    )
                )
    finally:
        if owns:
            http.close()
    return items


def research(client: httpx.Client | None = None) -> list[RawItem]:
    """PubMed. Used for effect sizes and outcomes, never for policy."""
    http, owns = _client(client)
    items: list[RawItem] = []
    try:
        for query in settings.care_research_queries:
            try:
                found = http.get(
                    PUBMED_SEARCH,
                    params={
                        "db": "pubmed",
                        "term": query,
                        "retmode": "json",
                        "retmax": "8",
                        "sort": "date",
                    },
                )
                found.raise_for_status()
                ids = found.json().get("esearchresult", {}).get("idlist", [])
                if not ids:
                    continue
                detail = http.get(
                    PUBMED_SUMMARY,
                    params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
                )
                detail.raise_for_status()
            except httpx.HTTPError:
                log.warning("pubmed query %r failed", query)
                continue
            result = detail.json().get("result", {})
            for pmid in result.get("uids", []):
                record = result.get(pmid, {})
                title = (record.get("title") or "").strip()
                if not title:
                    continue
                journal = record.get("fulljournalname") or record.get("source") or ""
                items.append(
                    RawItem(
                        source="pubmed",
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        title=title,
                        summary=f"{journal}. {record.get('elocationid', '')}".strip(),
                        external_id=str(pmid),
                        published_at=parse_date(record.get("pubdate")),
                        signals={
                            "evidence_kind": "research",
                            "audience_hint": "clinician",
                            "journal": journal,
                            "matched_query": query,
                        },
                    )
                )
    finally:
        if owns:
            http.close()
    return items


def industry_press(client: httpx.Client | None = None) -> list[RawItem]:
    """Trade and health-policy publications: what the field is reacting to this week."""
    http, owns = _client(client)
    items: list[RawItem] = []
    cutoff = _cutoff()
    try:
        for url in settings.care_policy_feeds:
            try:
                response = http.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("care feed %s failed", url)
                continue
            feed = feedparser.parse(response.text)
            publisher = (feed.feed.get("title") or url)[:64]
            for entry in feed.entries[:20]:
                title = (entry.get("title") or "").strip()
                if not title or not _relevant(title, entry.get("summary", "")):
                    continue
                published = parse_date(entry.get("published") or entry.get("updated"))
                if published and published < cutoff:
                    continue
                items.append(
                    RawItem(
                        source="industry_press",
                        url=entry.get("link", ""),
                        title=title,
                        summary=(entry.get("summary") or "")[:3000],
                        published_at=published,
                        signals={
                            "evidence_kind": "press",
                            "audience_hint": "provider",
                            "publisher": publisher,
                        },
                    )
                )
    finally:
        if owns:
            http.close()
    return items


def news(client: httpx.Client | None = None) -> list[RawItem]:
    """Google News over configured queries. This is also how LinkedIn-hosted articles
    reach us: we read what search surfaces publicly, we do not scrape the platform."""
    http, owns = _client(client)
    items: list[RawItem] = []
    cutoff = _cutoff()
    try:
        for query in settings.care_news_queries:
            params = urllib.parse.urlencode(
                {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
            )
            try:
                response = http.get(f"{GOOGLE_NEWS_RSS}?{params}")
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("news query %r failed", query)
                continue
            feed = feedparser.parse(response.text)
            for entry in feed.entries[:12]:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                published = parse_date(entry.get("published") or entry.get("updated"))
                if published and published < cutoff:
                    continue
                publisher = ""
                source_field = entry.get("source")
                if isinstance(source_field, dict):
                    publisher = str(source_field.get("title", ""))
                items.append(
                    RawItem(
                        source="news",
                        url=entry.get("link", ""),
                        title=title,
                        summary=(entry.get("summary") or "")[:2000],
                        published_at=published,
                        signals={
                            "evidence_kind": "press",
                            "audience_hint": "provider",
                            "publisher": publisher,
                            "matched_query": query,
                        },
                    )
                )
    finally:
        if owns:
            http.close()
    return items


def forums(client: httpx.Client | None = None) -> list[RawItem]:
    """Public caregiver communities, read through Reddit's public RSS.

    This is the one input that tells us what families actually struggle with. It is
    marked "signal" so the editor will not let a claim rest on it, and no usernames or
    personal details travel further than this function."""
    http, owns = _client(client)
    items: list[RawItem] = []
    try:
        for sub in settings.care_subreddits:
            try:
                response = http.get(f"https://www.reddit.com/r/{sub}/top/.rss?t=week")
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning("subreddit %s failed", sub)
                continue
            feed = feedparser.parse(response.text)
            for entry in feed.entries[:10]:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                items.append(
                    RawItem(
                        source="forum",
                        url=entry.get("link", ""),
                        title=title,
                        summary="",  # deliberately not stored: threads contain personal detail
                        published_at=parse_date(entry.get("published") or entry.get("updated")),
                        signals={
                            "evidence_kind": "signal",
                            "audience_hint": "caregiver",
                            "community": f"r/{sub}",
                        },
                    )
                )
    finally:
        if owns:
            http.close()
    return items


def _relevant(title: str, summary: str) -> bool:
    blob = f"{title} {summary}".lower()
    return any(keyword in blob for keyword in CAREGIVER_KEYWORDS)


FETCHERS = {
    "federal_register": federal_register,
    "research": research,
    "industry_press": industry_press,
    "news": news,
    "forums": forums,
}
