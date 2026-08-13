from __future__ import annotations

import datetime as dt

import httpx

from ..config import settings
from .base import RawItem, parse_date

SEARCH = "https://api.github.com/search/repositories"
RELEASES = "https://api.github.com/repos/{repo}/releases/latest"
TRENDING_WINDOW_DAYS = 14


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def fetch(client: httpx.Client | None = None) -> list[RawItem]:
    """Fast-rising ML repos plus releases of the repos we track. Code shipping is often
    the earliest credible signal that a research idea actually works."""
    owns_client = client is None
    client = client or httpx.Client(timeout=30, headers=_headers())
    items: list[RawItem] = []
    try:
        items.extend(_trending(client))
        items.extend(_releases(client))
    finally:
        if owns_client:
            client.close()
    return items


def _trending(client: httpx.Client) -> list[RawItem]:
    since = (dt.date.today() - dt.timedelta(days=TRENDING_WINDOW_DAYS)).isoformat()
    items: list[RawItem] = []
    for language in settings.github_trending_languages:
        params = {
            "q": f"created:>{since} language:{language} stars:>200",
            "sort": "stars",
            "order": "desc",
            "per_page": 15,
        }
        try:
            response = client.get(SEARCH, params=params, headers=_headers())
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        for repo in response.json().get("items", []):
            items.append(
                RawItem(
                    source="github_trending",
                    url=repo["html_url"],
                    external_id=repo["full_name"],
                    title=repo["full_name"],
                    summary=repo.get("description") or "",
                    published_at=parse_date(repo.get("created_at")),
                    signals={"stars": repo.get("stargazers_count", 0), "language": language},
                )
            )
    return items


def _releases(client: httpx.Client) -> list[RawItem]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    items: list[RawItem] = []
    for repo in settings.watch_repos:
        try:
            response = client.get(RELEASES.format(repo=repo), headers=_headers())
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        release = response.json()
        published = parse_date(release.get("published_at"))
        if published and published < cutoff:
            continue
        items.append(
            RawItem(
                source="github_release",
                url=release.get("html_url", ""),
                external_id=f"{repo}@{release.get('tag_name')}",
                title=f"{repo} {release.get('tag_name', '')}".strip(),
                summary=(release.get("body") or "")[:2000],
                published_at=published,
                signals={"repo": repo},
            )
        )
    return items
