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


@pytest.mark.parametrize(
    "body",
    [
        "This cuts memory use by ninety percent.",
        "Throughput is three times what the baseline managed.",
        "It is a two-orders-of-magnitude win.",
    ],
)
def test_blocks_ungrounded_numbers_written_as_words(brief, body):
    """Spelling a number out is the obvious way around a digit-only check."""
    draft = make_draft(brief, body, alt_text="chart")
    assert any(
        "not present in the brief" in note for note in deterministic_checks(draft, brief, [])
    )


def test_allows_word_numbers_that_are_in_the_brief(brief):
    brief.grounded_claims = [*(brief.grounded_claims or []), "Latency is three times lower."]
    draft = make_draft(brief, "Latency ends up three times lower on this workload.", alt_text="c")
    assert deterministic_checks(draft, brief, []) == []


def test_blocks_a_bare_ungrounded_multiplier(brief):
    draft = make_draft(brief, "The rewrite gives double the throughput.", alt_text="chart")
    assert any("not present in the brief" in n for n in deterministic_checks(draft, brief, []))


def test_a_digest_opening_is_blocked(brief):
    """ "This paper presents..." is what a summariser writes; the account shares finds."""
    draft = make_draft(brief, "This paper presents 3.2x lower end-to-end latency.")
    notes = deterministic_checks(draft, brief, [])
    assert any("digest entry" in note for note in notes)


def test_reading_a_paper_in_first_person_is_allowed(brief):
    draft = make_draft(
        brief,
        "Spent the morning on this one: 3.2x lower end-to-end latency, "
        "and only on one benchmark subset.",
    )
    assert deterministic_checks(draft, brief, []) == []


def test_an_em_dash_is_enough_to_block(brief):
    """One is the tell; the account writes with periods and commas."""
    draft = make_draft(brief, "Speculative tool calls — 3.2x lower end-to-end latency.")
    assert any("em dash" in note for note in deterministic_checks(draft, brief, []))
