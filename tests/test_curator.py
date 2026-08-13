from __future__ import annotations

import datetime as dt

from xswarm.agents import curator
from xswarm.llm import LLM
from xswarm.models import Item


def _item(session, title, **signals):
    item = Item(
        fingerprint=title,
        source="arxiv",
        url="u",
        title=title,
        summary="Agent tool use with 2x lower inference latency on a serving benchmark.",
        published_at=dt.datetime.now(dt.timezone.utc),
        signals=signals,
    )
    session.add(item)
    session.flush()
    return item


def test_novelty_penalises_repeats(session):
    item = _item(session, "Speculative tool calling for LLM agents")
    assert curator.score_novelty(item, []) == 1.0
    repeat = curator.score_novelty(item, ["speculative tool calling for llm agents"])
    assert repeat < 0.2


def test_momentum_is_log_compressed(session):
    low = curator.score_momentum(_item(session, "a", hf_upvotes=5))
    high = curator.score_momentum(_item(session, "b", hf_upvotes=500))
    assert low < high <= 1.0


def test_keyword_relevance_prefers_on_lane(session):
    on_lane = _item(session, "Agent tool use and inference latency")
    off_lane = Item(
        fingerprint="z",
        source="arxiv",
        url="u",
        title="Galaxy morphology",
        summary="astronomy",
    )
    assert curator._keyword_relevance(on_lane) > curator._keyword_relevance(off_lane)


def test_run_shortlists_and_persists(session):
    for index in range(12):
        _item(session, f"Agent inference latency study {index}", hf_upvotes=index * 10)
    candidates = curator.run(session, LLM(dry_run=True))
    assert 0 < len(candidates) <= 8
    assert candidates == sorted(candidates, key=lambda c: c.score, reverse=True)
    assert all(c.subscores for c in candidates)
