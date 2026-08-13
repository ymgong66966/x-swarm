from __future__ import annotations

import pytest
from conftest import make_draft

from xswarm.agents.editor import deterministic_checks

CLEAN = (
    "Speculative tool calls cut agent latency 3.2x lower end-to-end latency. "
    "Only evaluated on one benchmark subset, so treat it as a lead, not a result."
)


def test_clean_draft_passes(brief):
    draft = make_draft(brief, CLEAN, alt_text="Diagram of speculative tool calling")
    assert deterministic_checks(draft, brief, []) == []


def test_blocks_ungrounded_number(brief):
    draft = make_draft(brief, "This gives a 9.9x speedup.", alt_text="chart")
    assert any("9.9" in note for note in deterministic_checks(draft, brief, []))


def test_blocks_url_and_hashtag(brief):
    draft = make_draft(brief, "Great work #AI https://example.com", alt_text="chart")
    notes = deterministic_checks(draft, brief, [])
    assert any("URL" in n for n in notes)
    assert any("hashtag" in n for n in notes)


def test_blocks_banned_phrase(brief):
    draft = make_draft(brief, "This is a game changer for agents.", alt_text="chart")
    assert any("banned phrase" in n for n in deterministic_checks(draft, brief, []))


FABRICATED = [
    "I ran this locally and it held up.",
    "In my tests the tool calls stayed reliable.",
    "I've run it on 4 GPUs.",
    "I have tested this in prod.",
    "I spun it up locally.",
    "My benchmarks show the same gap.",
]

# Ordinary operator vocabulary that must survive the gate.
NOT_FABRICATED = [
    "We ran out of memory at 32k context.",
    "I ran into the same wall last year.",
    "The authors ran 3 seeds.",
    "In my experience, retries cover most of this.",
]


@pytest.mark.parametrize("body", FABRICATED)
def test_blocks_fabricated_firsthand_experience(brief, body):
    draft = make_draft(brief, body, alt_text="chart")
    assert any("first-hand" in n for n in deterministic_checks(draft, brief, []))


@pytest.mark.parametrize("body", NOT_FABRICATED)
def test_allows_operator_idioms(brief, body):
    draft = make_draft(brief, body, alt_text="chart")
    assert not any("first-hand" in n for n in deterministic_checks(draft, brief, []))


def test_blocks_unverified_claim(brief):
    draft = make_draft(brief, "Works for any agent framework you use.", alt_text="chart")
    assert any("unverified" in n for n in deterministic_checks(draft, brief, []))


def test_blocks_near_duplicate(brief):
    draft = make_draft(brief, CLEAN, alt_text="chart")
    assert any("duplicate" in n for n in deterministic_checks(draft, brief, [CLEAN]))


def test_blocks_missing_alt_text(brief):
    draft = make_draft(brief, CLEAN, features={"visual_hint": "result_chart"})
    assert any("alt text" in n for n in deterministic_checks(draft, brief, []))


def test_blocks_when_brief_has_no_grounded_claims(brief):
    brief.grounded_claims = []
    draft = make_draft(brief, "A confident claim with no grounding.", alt_text="chart")
    assert any("grounded claims" in n for n in deterministic_checks(draft, brief, []))


def test_blocks_overlong_post(brief):
    draft = make_draft(brief, "x" * 400, alt_text="chart")
    assert any("too long" in n for n in deterministic_checks(draft, brief, []))
