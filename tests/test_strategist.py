from __future__ import annotations

import datetime as dt

from conftest import make_draft

from xswarm.agents import strategist
from xswarm.config import settings
from xswarm.llm import LLM
from xswarm.models import PostMetric, Publication


class FakeLLM(LLM):
    def __init__(self, text):
        self.text = text
        self.dry_run = False
        self.provider = "fake"
        self.prompt = ""

    def complete(self, prompt, **kwargs):
        self.prompt = prompt
        return self.text


def add_post(session, brief, *, hook, pillar, impressions, likes, variant):
    draft = make_draft(brief, f"post {variant}", features={"hook_style": hook, "pillar": pillar})
    draft.variant = variant
    session.add(draft)
    session.flush()
    publication = Publication(
        draft_id=draft.id,
        status="published",
        published_at=dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc),
    )
    session.add(publication)
    session.flush()
    session.add(
        PostMetric(
            publication_id=publication.id,
            captured_at=dt.datetime.now(dt.timezone.utc),
            impressions=impressions,
            likes=likes,
        )
    )
    session.flush()


def test_aggregates_by_hook_and_pillar(session, brief):
    add_post(session, brief, hook="number", pillar="paper", impressions=1000, likes=50, variant=0)
    add_post(session, brief, hook="number", pillar="paper", impressions=1000, likes=30, variant=1)
    add_post(session, brief, hook="claim", pillar="paper", impressions=1000, likes=5, variant=2)

    groups = {(g.dimension, g.value): g for g in strategist.aggregate(session)}

    assert groups[("hook_style", "number")].posts == 2
    assert groups[("hook_style", "number")].engagement_rate == 0.04
    assert groups[("hook_style", "claim")].engagement_rate == 0.005
    assert groups[("visual", "none")].posts == 3
    assert ("hour_local", "08") in groups


def test_only_the_latest_snapshot_per_post_counts(session, brief):
    add_post(session, brief, hook="number", pillar="paper", impressions=100, likes=1, variant=0)
    publication = session.query(Publication).one()
    session.add(
        PostMetric(
            publication_id=publication.id,
            captured_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            impressions=900,
            likes=90,
        )
    )
    session.flush()
    groups = {(g.dimension, g.value): g for g in strategist.aggregate(session)}
    assert groups[("hook_style", "number")].impressions == 900


def test_playbook_is_left_alone_below_the_sample_floor(session, brief, monkeypatch):
    monkeypatch.setattr(settings, "strategy_min_posts", 5)
    add_post(session, brief, hook="number", pillar="paper", impressions=100, likes=1, variant=0)
    result = strategist.run(session, FakeLLM("NEW PLAYBOOK"))
    assert "playbook left unchanged" in result


def test_playbook_is_rewritten_with_measured_numbers(session, brief, tmp_path, monkeypatch):
    playbook = tmp_path / "playbook.md"
    playbook.write_text("# old")
    monkeypatch.setattr(settings, "playbook_path", playbook)
    monkeypatch.setattr(settings, "strategy_min_posts", 2)
    add_post(session, brief, hook="number", pillar="paper", impressions=100, likes=10, variant=0)
    add_post(session, brief, hook="claim", pillar="paper", impressions=100, likes=1, variant=1)

    llm = FakeLLM("# new playbook")
    strategist.run(session, llm)

    assert playbook.read_text().strip() == "# new playbook"
    assert "hook_style=number" in llm.prompt
    assert "# old" in llm.prompt
    archived = tmp_path / "strategy" / f"{dt.date.today().isoformat()}.md"
    assert "hook_style=number" in archived.read_text()
