from __future__ import annotations

import datetime as dt

from conftest import make_draft
from sqlalchemy.orm import Session

from xswarm.agents import composer
from xswarm.agents.editor import deterministic_checks
from xswarm.config import settings
from xswarm.models import Brief, Candidate, Item


class StubLLM:
    def __init__(self, payload):
        self.payload = payload
        self.dry_run = False

    def complete_json(self, prompt, **kwargs):
        return self.payload


def test_eligible_requires_pillar_and_enough_claims(brief: Brief):
    brief.grounded_claims = ["a", "b", "c", "d"]
    draft = make_draft(brief, "body", features={"pillar": "paper_of_the_day"})
    assert composer.eligible(draft)

    draft.features = {"pillar": "hot_take"}
    assert not composer.eligible(draft)

    draft.features = {"pillar": "paper_of_the_day"}
    brief.grounded_claims = ["a"]
    assert not composer.eligible(draft)


def test_thread_is_stored_and_truncated(session: Session, brief: Brief):
    brief.grounded_claims = ["a", "b", "c", "d"]
    draft = make_draft(brief, "opening", features={"pillar": "paper_of_the_day"})
    session.add(draft)
    session.flush()

    llm = StubLLM(["second post", "x" * 500] + ["filler"] * 10)
    expanded = composer.run(session, llm, [draft])

    assert expanded == [draft]
    assert len(draft.thread) == settings.max_thread_posts
    assert draft.features["thread"] is True
    # Overlong model output is left intact and blocked, not truncated mid-sentence.
    assert any("too long" in note for note in deterministic_checks(draft, brief, []))


def test_fallback_thread_uses_brief_material(brief: Brief):
    brief.grounded_claims = ["claim one", "claim two", "claim three", "claim four"]
    posts = composer._fallback_thread(brief)
    assert "claim two" in posts
    assert brief.builder_takeaway in posts


def _candidate(session: Session, index: int) -> Candidate:
    item = Item(
        fingerprint=f"fp-week-{index}",
        source="arxiv",
        url=f"https://arxiv.org/abs/{index}",
        title=f"Paper {index} on inference latency",
        summary=f"Summary {index}",
    )
    session.add(item)
    session.flush()
    candidate = Candidate(item_id=item.id, run_date=dt.date.today(), score=1.0 - index / 100)
    session.add(candidate)
    session.flush()
    return candidate


def test_weekly_roundup_needs_three_picks(session: Session):
    _candidate(session, 1)
    _candidate(session, 2)
    assert composer.weekly_roundup(session, StubLLM(None)) is None


def test_weekly_roundup_builds_a_briefless_thread(session: Session):
    for index in range(4):
        _candidate(session, index)

    draft = composer.weekly_roundup(session, StubLLM(None))

    assert draft is not None
    assert draft.brief_id is None
    assert draft.thread
    assert draft.link_reply.startswith("Links:")
    # A brief-less draft carries its own grounding so the Editor can still check numbers.
    assert "Paper 0" in draft.features["grounding"]
    assert deterministic_checks(draft, None, []) == []


def test_editor_checks_every_thread_post(brief: Brief):
    draft = make_draft(brief, "clean opening", alt_text="chart")
    draft.thread = ["fine", "this claims a 9.9x speedup"]
    notes = deterministic_checks(draft, brief, [])
    assert any("9.9" in note and "thread post 3" in note for note in notes)
