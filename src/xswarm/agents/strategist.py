"""Weekly learning loop.

Aggregates the latest metric snapshot per post by the dimensions we actually control
(pillar, hook style, visual template, posting hour), then asks the model to rewrite
`playbook.md` — which the Writer reads on every run, closing the loop. The aggregation
is deterministic; the model only turns numbers into rules.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Draft, PostMetric, Publication

log = logging.getLogger(__name__)


@dataclass
class Group:
    dimension: str
    value: str
    posts: int = 0
    impressions: int = 0
    engagements: int = 0
    bookmarks: int = 0
    profile_clicks: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def engagement_rate(self) -> float:
        return self.engagements / self.impressions if self.impressions else 0.0

    @property
    def profile_click_rate(self) -> float:
        return self.profile_clicks / self.impressions if self.impressions else 0.0

    def line(self) -> str:
        return (
            f"{self.dimension}={self.value}: {self.posts} posts, "
            f"{self.impressions // max(self.posts, 1)} median-ish impressions/post, "
            f"engagement {self.engagement_rate:.2%}, "
            f"bookmarks {self.bookmarks}, "
            f"profile clicks {self.profile_click_rate:.2%}"
        )


def _latest_metrics(session: Session, since: dt.datetime) -> list[tuple[Publication, PostMetric]]:
    rows = session.execute(
        select(Publication, PostMetric)
        .join(PostMetric, PostMetric.publication_id == Publication.id)
        .where(PostMetric.captured_at >= since)
        .order_by(PostMetric.captured_at)
    ).all()
    latest: dict[int, tuple[Publication, PostMetric]] = {}
    for publication, metric in rows:
        latest[publication.id] = (publication, metric)
    return list(latest.values())


def _dimensions(draft: Draft, publication: Publication) -> list[tuple[str, str]]:
    features = draft.features or {}
    dims = [
        ("pillar", str(features.get("pillar", "unknown"))),
        ("hook_style", str(features.get("hook_style", "unknown"))),
        ("visual", draft.assets[0].kind if draft.assets else "none"),
    ]
    when = publication.published_at or publication.scheduled_for
    if when is not None:
        local = when.astimezone(ZoneInfo(settings.publish_timezone))
        dims.append(("hour_local", f"{local.hour:02d}"))
    return dims


def aggregate(session: Session, *, days: int = 28) -> list[Group]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    groups: dict[tuple[str, str], Group] = {}
    for publication, metric in _latest_metrics(session, since):
        draft = publication.draft
        engagements = metric.likes + metric.replies + metric.reposts + metric.quotes
        for dimension, value in _dimensions(draft, publication):
            group = groups.setdefault((dimension, value), Group(dimension, value))
            group.posts += 1
            group.impressions += metric.impressions
            group.engagements += engagements
            group.bookmarks += metric.bookmarks
            group.profile_clicks += metric.profile_clicks
            if len(group.examples) < 2:
                group.examples.append(draft.body[:100])
    return sorted(
        groups.values(), key=lambda g: (g.dimension, -g.engagement_rate, -g.impressions)
    )


def report(groups: list[Group]) -> str:
    return "\n".join(group.line() for group in groups)


def run(session: Session, llm: LLM, *, days: int = 28, write_playbook: bool = True) -> str:
    groups = aggregate(session, days=days)
    posts = max((g.posts for g in groups if g.dimension == "pillar"), default=0)
    if posts < settings.strategy_min_posts:
        message = (
            f"only {posts} measured posts in the last {days} days "
            f"(need {settings.strategy_min_posts}); playbook left unchanged"
        )
        log.info(message)
        return message

    current = settings.playbook_path.read_text() if settings.playbook_path.exists() else ""
    updated = llm.complete(
        load_prompt("strategist").format(
            days=days,
            posts=posts,
            performance=report(groups),
            playbook=current,
        ),
        strong=True,
        max_tokens=2500,
        agent="strategist",
    )
    if not updated:
        return report(groups)
    if write_playbook:
        # A dated copy next to the playbook keeps the reasoning even though the
        # playbook itself is overwritten each week.
        stamp = dt.date.today().isoformat()
        (settings.playbook_path.parent / f"strategy/{stamp}.md").parent.mkdir(
            parents=True, exist_ok=True
        )
        (settings.playbook_path.parent / f"strategy/{stamp}.md").write_text(
            f"# Strategy review {stamp}\n\n## Measured\n\n```\n{report(groups)}\n```\n"
        )
        settings.playbook_path.write_text(updated.strip() + "\n")
        log.info("playbook updated from %d posts", posts)
    return updated
