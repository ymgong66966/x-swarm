"""One page that answers "what did each stream produce, and did anyone see it".

Both streams are reduced to the same shape — produced, cleared the gate, published,
reach, engagement, cost — so ML posts and care articles can be compared directly, which
is the point of running them side by side.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    STREAM_CARE,
    STREAM_ML,
    Article,
    CrawlCheck,
    Draft,
    ModelCall,
    PostMetric,
    Publication,
    TrafficSnapshot,
)

log = logging.getLogger(__name__)

ML_AGENTS = {"curator", "analyst", "writer", "composer", "editor", "visualizer", "strategist"}
CARE_AGENTS = {"care_angle", "care_writer", "care_promoter"}


@dataclass(slots=True)
class StreamSummary:
    stream: str
    drafted: int = 0
    ready: int = 0
    blocked: int = 0
    published: int = 0
    impressions: int = 0
    engagements: int = 0
    link_clicks: int = 0
    cost_usd: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.ready / self.drafted if self.drafted else 0.0

    @property
    def engagement_rate(self) -> float:
        return self.engagements / self.impressions if self.impressions else 0.0


@dataclass(slots=True)
class ArticleRow:
    title: str
    slug: str
    audience: str
    pillar: str
    status: str
    words: int
    sources: int
    visitors: int
    pageviews: int
    top_referrer: str


@dataclass(slots=True)
class PostRow:
    stream: str
    body: str
    status: str
    impressions: int
    engagements: int
    link_clicks: int
    url: str


@dataclass(slots=True)
class CrawlRow:
    url: str
    status_code: int
    robots_allowed: bool
    in_sitemap: bool
    indexable: bool
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Dashboard:
    generated_at: dt.datetime
    since: dt.date
    streams: list[StreamSummary]
    articles: list[ArticleRow]
    posts: list[PostRow]
    crawl: list[CrawlRow]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "since": self.since.isoformat(),
            "streams": [
                {**asdict(s), "pass_rate": s.pass_rate, "engagement_rate": s.engagement_rate}
                for s in self.streams
            ],
            "articles": [asdict(a) for a in self.articles],
            "posts": [asdict(p) for p in self.posts],
            "crawl": [asdict(c) for c in self.crawl],
        }


def _latest_metric(publication: Publication) -> PostMetric | None:
    if not publication.metrics:
        return None
    return max(publication.metrics, key=lambda m: m.captured_at)


def _engagements(metric: PostMetric) -> int:
    return metric.likes + metric.replies + metric.reposts + metric.quotes + metric.bookmarks


def _stream_summary(session: Session, stream: str, since: dt.date) -> StreamSummary:
    summary = StreamSummary(stream=stream)
    drafts = list(
        session.scalars(
            select(Draft).where(Draft.stream == stream, Draft.created_at >= _as_datetime(since))
        )
    )
    summary.drafted = len(drafts)
    live = ("ready_for_review", "approved", "scheduled")
    summary.ready = sum(1 for d in drafts if d.status in live)
    summary.blocked = sum(1 for d in drafts if d.status == "blocked")

    for draft in drafts:
        publication = draft.publication
        if publication is None:
            continue
        summary.published += 1
        metric = _latest_metric(publication)
        if metric:
            summary.impressions += metric.impressions
            summary.engagements += _engagements(metric)
            summary.link_clicks += metric.link_clicks

    agents = CARE_AGENTS if stream == STREAM_CARE else ML_AGENTS
    summary.cost_usd = sum(
        call.cost_usd
        for call in session.scalars(select(ModelCall).where(ModelCall.run_date >= since))
        if call.agent in agents
    )
    return summary


def _as_datetime(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)


def _article_rows(session: Session, since: dt.date) -> list[ArticleRow]:
    snapshots = {s.url: s for s in session.scalars(select(TrafficSnapshot))}
    rows: list[ArticleRow] = []
    for article in session.scalars(
        select(Article).where(Article.run_date >= since).order_by(Article.run_date.desc())
    ):
        snapshot = snapshots.get(article.published_url or "")
        referrers = snapshot.referrers if snapshot else {}
        top = max(referrers.items(), key=lambda kv: kv[1])[0] if referrers else "—"
        rows.append(
            ArticleRow(
                title=article.title,
                slug=article.slug,
                audience=article.audience,
                pillar=article.pillar,
                status=article.status,
                words=article.word_count,
                sources=len(article.sources),
                visitors=snapshot.visitors if snapshot else 0,
                pageviews=snapshot.pageviews if snapshot else 0,
                top_referrer=str(top),
            )
        )
    return rows


def _post_rows(session: Session, since: dt.date, limit: int = 40) -> list[PostRow]:
    rows: list[PostRow] = []
    for draft in session.scalars(
        select(Draft).where(Draft.created_at >= _as_datetime(since)).order_by(Draft.id.desc())
    ):
        publication = draft.publication
        metric = _latest_metric(publication) if publication else None
        rows.append(
            PostRow(
                stream=draft.stream,
                body=draft.body[:180],
                status=draft.status,
                impressions=metric.impressions if metric else 0,
                engagements=_engagements(metric) if metric else 0,
                link_clicks=metric.link_clicks if metric else 0,
                url=(publication.post_url or "") if publication else "",
            )
        )
        if len(rows) >= limit:
            break
    return rows


def _crawl_rows(session: Session) -> list[CrawlRow]:
    latest: dict[str, CrawlCheck] = {}
    for check in session.scalars(select(CrawlCheck).order_by(CrawlCheck.checked_at)):
        latest[check.url] = check
    return [
        CrawlRow(
            url=check.url,
            status_code=check.status_code,
            robots_allowed=check.robots_allowed,
            in_sitemap=check.in_sitemap,
            indexable=check.indexable,
            issues=list(check.issues),
        )
        for check in latest.values()
    ]


def build(session: Session, *, days: int | None = None) -> Dashboard:
    days = days or settings.dashboard_lookback_days
    since = dt.date.today() - dt.timedelta(days=days)
    return Dashboard(
        generated_at=dt.datetime.now(dt.timezone.utc),
        since=since,
        streams=[_stream_summary(session, s, since) for s in (STREAM_ML, STREAM_CARE)],
        articles=_article_rows(session, since),
        posts=_post_rows(session, since),
        crawl=_crawl_rows(session),
    )


def _e(value: object) -> str:
    return html.escape(str(value))


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_e(empty)}</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(dashboard: Dashboard) -> str:
    generated = _e(dashboard.generated_at.strftime("%Y-%m-%d %H:%M UTC"))
    cards = "".join(
        f"""<div class="card">
  <h3>{_e(summary.stream)} stream</h3>
  <p class="big">{summary.drafted}<span> pieces</span></p>
  <dl>
    <dt>cleared the gate</dt><dd>{summary.ready} ({summary.pass_rate:.0%})</dd>
    <dt>blocked</dt><dd>{summary.blocked}</dd>
    <dt>published</dt><dd>{summary.published}</dd>
    <dt>impressions</dt><dd>{summary.impressions:,}</dd>
    <dt>engagement rate</dt><dd>{summary.engagement_rate:.2%}</dd>
    <dt>link clicks</dt><dd>{summary.link_clicks:,}</dd>
    <dt>model cost</dt><dd>${summary.cost_usd:.2f}</dd>
  </dl>
</div>"""
        for summary in dashboard.streams
    )

    articles = _table(
        [
            "article",
            "audience",
            "pillar",
            "status",
            "words",
            "sources",
            "visitors",
            "views",
            "top referrer",
        ],
        [
            [
                _e(row.title),
                _e(row.audience),
                _e(row.pillar),
                _e(row.status),
                str(row.words),
                str(row.sources),
                str(row.visitors),
                str(row.pageviews),
                _e(row.top_referrer),
            ]
            for row in dashboard.articles
        ],
        "No articles in this window.",
    )

    posts = _table(
        ["stream", "post", "status", "impressions", "engagements", "clicks"],
        [
            [
                _e(row.stream),
                f'<a href="{_e(row.url)}">{_e(row.body)}</a>' if row.url else _e(row.body),
                _e(row.status),
                str(row.impressions),
                str(row.engagements),
                str(row.link_clicks),
            ]
            for row in dashboard.posts
        ],
        "No posts in this window.",
    )

    crawl = _table(
        ["url", "status", "robots", "sitemap", "indexable", "issues"],
        [
            [
                f'<a href="{_e(row.url)}">{_e(row.url)}</a>',
                str(row.status_code),
                "allowed" if row.robots_allowed else "BLOCKED",
                "yes" if row.in_sitemap else "no",
                "yes" if row.indexable else "NO",
                _e("; ".join(row.issues) or "—"),
            ]
            for row in dashboard.crawl
        ],
        "No crawl checks yet — run `xswarm crawl`.",
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>xswarm dashboard</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; margin: 2rem auto;
        max-width: 1100px; padding: 0 1rem; }}
 h1 {{ margin-bottom: 0; }}
 .meta {{ color: #777; margin-top: .25rem; }}
 .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }}
 .card {{ border: 1px solid #8883; border-radius: 10px; padding: 1rem 1.25rem; flex: 1 1 260px; }}
 .card h3 {{ margin: 0; text-transform: uppercase; letter-spacing: .08em;
            font-size: .75rem; color: #777; }}
 .big {{ font-size: 2.2rem; margin: .25rem 0 .75rem; }}
 .big span {{ font-size: .9rem; color: #777; }}
 dl {{ display: grid; grid-template-columns: 1fr auto; gap: .15rem .5rem; margin: 0; }}
 dt {{ color: #777; }} dd {{ margin: 0; text-align: right; font-variant-numeric: tabular-nums; }}
 table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
 th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8882;
          vertical-align: top; }}
 th {{ font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; color: #777; }}
 .empty {{ color: #777; font-style: italic; }}
 a {{ color: inherit; }}
</style></head>
<body>
<h1>xswarm</h1>
<p class="meta">both streams, {_e(dashboard.since.isoformat())} → today · generated {generated}</p>
<div class="cards">{cards}</div>
<h2>Articles</h2>{articles}
<h2>Posts</h2>{posts}
<h2>Crawlability &amp; SEO</h2>{crawl}
</body></html>
"""


def write(session: Session, *, days: int | None = None) -> str:
    dashboard = build(session, days=days)
    path = settings.dashboard_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(dashboard))
    log.info("dashboard written to %s", path)
    return str(path)
