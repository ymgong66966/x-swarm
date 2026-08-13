"""Brand memory: what the account has actually said, as opposed to what it looked at.

The Curator's novelty score compares against everything we *considered*, which is the
wrong bar — the failure the audience notices is the same paper or the same framing going
out twice. This module reads the published/approved side of the database and is used to
(a) hard-block repeat topics inside the cooldown and (b) show the Writer its own recent
openings so it stops reaching for the same one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Brief, Candidate, Draft, Item
from .sources import normalize_title

# Statuses that mean "this text represents the account", not "this text was one of
# three options the editor looked at".
COMMITTED = ("approved", "scheduled", "published")
TOPIC_REPEAT_THRESHOLD = 82


@dataclass(frozen=True)
class Post:
    posted_on: dt.date
    pillar: str
    title: str
    url: str
    opening: str
    hook_style: str


def _cutoff(days: int | None) -> dt.datetime:
    days = settings.repeat_topic_cooldown_days if days is None else days
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


def committed_posts(session: Session, *, days: int | None = None) -> list[Post]:
    """Everything the account committed to inside the window, newest first."""
    rows = session.execute(
        select(Draft, Item)
        .outerjoin(Brief, Draft.brief_id == Brief.id)
        .outerjoin(Candidate, Brief.candidate_id == Candidate.id)
        .outerjoin(Item, Candidate.item_id == Item.id)
        .where(Draft.status.in_(COMMITTED), Draft.created_at >= _cutoff(days))
        .order_by(Draft.created_at.desc())
    ).all()

    posts: list[Post] = []
    for draft, item in rows:
        features = draft.features or {}
        posts.append(
            Post(
                posted_on=draft.created_at.date(),
                pillar=str(features.get("pillar", "")),
                title=item.title if item else str(features.get("topic", "")),
                url=item.url if item else "",
                opening=draft.body.strip().split("\n", 1)[0][:120],
                hook_style=str(features.get("hook_style", "")),
            )
        )
    return posts


def covered_titles(session: Session, *, days: int | None = None) -> list[str]:
    return [normalize_title(p.title) for p in committed_posts(session, days=days) if p.title]


def is_repeat(title: str, covered: list[str]) -> bool:
    """True when we already posted about this inside the cooldown."""
    normalized = normalize_title(title)
    if not normalized or not covered:
        return False
    return max(fuzz.token_set_ratio(normalized, past) for past in covered) >= TOPIC_REPEAT_THRESHOLD


def writer_context(session: Session, *, limit: int = 8) -> str:
    """Recent openings, rendered for the Writer prompt. Empty string on a cold start."""
    posts = committed_posts(session)[:limit]
    if not posts:
        return "(nothing posted yet)"
    return "\n".join(
        f"- {p.posted_on.isoformat()} [{p.pillar or 'unknown'}/{p.hook_style or 'unknown'}] "
        f"{p.opening}"
        for p in posts
    )
