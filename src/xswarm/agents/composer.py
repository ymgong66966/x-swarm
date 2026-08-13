"""Composer: turns a single post into a thread when the material justifies one.

Threads are the exception, not the default. A thread that says less than the single post
would have is worse than the single post, so only briefs with enough verified material
get expanded, and the weekly roundup — which is inherently a list — is always a thread.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Brief, Candidate, Draft, Item

log = logging.getLogger(__name__)


def _read(path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def eligible(draft: Draft) -> bool:
    """Enough verified material to keep a reader through four posts."""
    brief = draft.brief
    if brief is None or draft.thread:
        return False
    if (draft.features or {}).get("pillar") not in settings.thread_pillars:
        return False
    return len(brief.grounded_claims or []) >= settings.thread_min_claims


def _fallback_thread(brief: Brief) -> list[str]:
    """One claim per post, then the caveat and the takeaway. Dull but honest."""
    posts = [c.strip() for c in (brief.grounded_claims or [])[1:4] if c.strip()]
    for extra in (brief.what_it_replaces, brief.caveat, brief.builder_takeaway):
        if extra and extra.strip():
            posts.append(extra.strip())
    return [p[: settings.max_post_chars] for p in posts][: settings.max_thread_posts]


def expand(draft: Draft, llm: LLM, voice: str) -> list[str]:
    brief = draft.brief
    if brief is None:
        return []
    payload = llm.complete_json(
        load_prompt("composer").format(
            voice=voice,
            opening=draft.body,
            max_chars=settings.max_post_chars,
            max_posts=settings.max_thread_posts,
            whats_new=brief.whats_new,
            what_it_replaces=brief.what_it_replaces,
            key_number=brief.key_number,
            caveat=brief.caveat,
            builder_takeaway=brief.builder_takeaway,
            grounded_claims="\n".join(f"- {c}" for c in brief.grounded_claims or []),
            summary=brief.candidate.item.summary[:2000],
        ),
        strong=True,
        max_tokens=1500,
        agent="composer",
    )
    if isinstance(payload, list) and payload:
        # Not truncated: half a sentence is worse than a post the Editor blocks.
        posts = [str(p).strip() for p in payload if str(p).strip()]
        return posts[: settings.max_thread_posts]
    return _fallback_thread(brief)


def run(session: Session, llm: LLM, drafts: list[Draft]) -> list[Draft]:
    """Expand the eligible drafts in place. Returns the ones that became threads."""
    voice = _read(settings.voice_path)
    expanded: list[Draft] = []
    for draft in drafts:
        if not eligible(draft):
            continue
        posts = expand(draft, llm, voice)
        if not posts:
            continue
        draft.thread = posts
        draft.features = {**(draft.features or {}), "thread": True}
        expanded.append(draft)
    session.flush()
    log.info("composed %d threads", len(expanded))
    return expanded


def _week_picks(session: Session, run_date: dt.date) -> list[tuple[Candidate, Item]]:
    since = run_date - dt.timedelta(days=7)
    rows = session.execute(
        select(Candidate, Item)
        .join(Item, Candidate.item_id == Item.id)
        .where(Candidate.run_date >= since, Candidate.run_date <= run_date)
        .order_by(Candidate.score.desc())
    ).all()

    picks: list[tuple[Candidate, Item]] = []
    seen: set[str] = set()
    for candidate, item in rows:
        if item.url in seen:
            continue
        seen.add(item.url)
        picks.append((candidate, item))
        if len(picks) >= settings.roundup_picks:
            break
    return picks


def weekly_roundup(session: Session, llm: LLM, run_date: dt.date | None = None) -> Draft | None:
    """A thread of the week's highest-scoring items. Not tied to one brief, so its
    grounding travels with the draft for the Editor to check numbers against."""
    run_date = run_date or dt.date.today()
    picks = _week_picks(session, run_date)
    if len(picks) < 3:
        log.info("only %d picks this week — skipping the roundup", len(picks))
        return None

    entries = "\n".join(
        f"{i + 1}. {item.title} ({item.source}) — {item.summary[:300]}"
        for i, (_, item) in enumerate(picks)
    )
    payload = llm.complete_json(
        load_prompt("roundup").format(
            voice=_read(settings.voice_path),
            max_chars=settings.max_post_chars,
            count=len(picks),
            entries=entries,
        ),
        strong=True,
        max_tokens=2000,
        agent="composer",
    )
    if isinstance(payload, list) and len(payload) >= 2:
        posts = [str(p).strip() for p in payload if str(p).strip()]
    else:
        posts = [f"{len(picks)} things worth reading from this week in ML:"] + [
            f"{i + 1}. {item.title[: settings.max_post_chars - 20]}"
            for i, (_, item) in enumerate(picks)
        ]

    links = "\n".join(item.url for _, item in picks if item.url)
    draft = Draft(
        brief_id=None,
        variant=0,
        body=posts[0],
        thread=posts[1:],
        link_reply=f"Links:\n{links}" if links else "",
        features={
            "pillar": "curation",
            "hook_style": "roundup",
            "thread": True,
            "topic": f"weekly roundup {run_date.isoformat()}",
            # The picks, verbatim and numbered, are the only thing this thread may assert.
            "grounding": f"{len(picks)} picks\n{entries}",
        },
    )
    session.add(draft)
    session.flush()
    log.info("composed weekly roundup from %d picks", len(picks))
    return draft
