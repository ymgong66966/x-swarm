"""Per-article scorecard: is the page findable, and did X send anyone to it.

One row per published article, because that is the unit that either compounds or does
not. The dashboard shows the streams in aggregate, which hides the thing worth knowing —
whether *this* article is indexable and whether *its* promos produced link clicks.

Two of the four measurement layers are readable today: technical crawl (`xswarm crawl`)
and X attention (Typefully → `post_metrics`). Search Console impressions and on-site
conversion are deliberately absent columns rather than fabricated ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Article, CrawlCheck


@dataclass(slots=True)
class Scorecard:
    article_id: int
    slug: str
    audience: str
    status: str
    url: str
    indexable: bool | None
    in_sitemap: bool | None
    issues: list[str]
    promos: int
    scheduled: int
    impressions: int
    link_clicks: int

    @property
    def click_rate(self) -> float:
        """Link clicks per impression — the only honest read on whether a post moved
        anyone off the timeline and onto the page."""
        return self.link_clicks / self.impressions if self.impressions else 0.0


def _latest_checks(session: Session) -> dict[str, CrawlCheck]:
    latest: dict[str, CrawlCheck] = {}
    for check in session.scalars(select(CrawlCheck).order_by(CrawlCheck.checked_at)):
        latest[check.url] = check
    return latest


def build(session: Session) -> list[Scorecard]:
    checks = _latest_checks(session)
    rows: list[Scorecard] = []
    for article in session.scalars(select(Article).order_by(Article.id)):
        url = article.published_url or ""
        check = checks.get(url) or checks.get(url.rstrip("/"))
        impressions = clicks = scheduled = 0
        for draft in article.promos:
            publication = draft.publication
            if publication is None:
                continue
            scheduled += 1
            if not publication.metrics:
                continue
            metric = max(publication.metrics, key=lambda m: m.captured_at)
            impressions += metric.impressions
            clicks += metric.link_clicks
        rows.append(
            Scorecard(
                article_id=article.id,
                slug=article.slug,
                audience=article.audience,
                status=article.status,
                url=url,
                indexable=check.indexable if check else None,
                in_sitemap=check.in_sitemap if check else None,
                issues=list(check.issues) if check else [],
                promos=len(article.promos),
                scheduled=scheduled,
                impressions=impressions,
                link_clicks=clicks,
            )
        )
    return rows
