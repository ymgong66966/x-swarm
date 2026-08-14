"""Curation, promos and stream isolation for the care pipeline."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from xswarm.analytics import dashboard
from xswarm.care import curator, export, promoter, writer
from xswarm.care.angle import Evidence, Plan
from xswarm.config import settings
from xswarm.models import STREAM_CARE, STREAM_ML, Article, Draft, Item

TODAY = dt.date.today()


def make_item(session: Session, **kwargs) -> Item:
    defaults = {
        "fingerprint": kwargs.get("external_id", "care-1"),
        "source": "federal_register",
        "external_id": "care-1",
        "url": "https://www.federalregister.gov/documents/1",
        "title": "CY 2025 Physician Fee Schedule adds caregiver training to telehealth",
        "summary": "Caregiver training services under codes 97550 and G0541.",
        "stream": STREAM_CARE,
        "published_at": dt.datetime.now(dt.timezone.utc),
        "signals": {"evidence_kind": "regulatory", "audience_hint": "provider"},
    }
    defaults.update(kwargs)
    item = Item(**defaults)
    session.add(item)
    session.flush()
    return item


def test_curator_ranks_regulatory_above_forum_chatter(session: Session) -> None:
    make_item(session)
    make_item(
        session,
        fingerprint="care-2",
        external_id="care-2",
        source="forum",
        url="https://www.reddit.com/r/CaregiverSupport/comments/x",
        title="Anyone else exhausted caring for a parent with dementia at home?",
        summary="Venting about caregiver burnout at home.",
        signals={"evidence_kind": "signal", "audience_hint": "caregiver"},
    )
    candidates = curator.run(session, run_date=TODAY)
    assert [c.pillar for c in candidates][0] == "policy_explainer"
    assert candidates[0].score > candidates[-1].score
    assert {c.stream for c in candidates} == {STREAM_CARE}


def test_curator_ignores_ml_items(session: Session) -> None:
    make_item(
        session,
        stream=STREAM_ML,
        source="arxiv",
        url="https://arxiv.org/abs/1",
        title="Caregiver training for LLM agents in Medicare telehealth",
    )
    assert curator.run(session, run_date=TODAY) == []


def test_curator_drops_off_subject_items(session: Session) -> None:
    make_item(session, title="Quarterly earnings for a shipping company", summary="Freight rates.")
    assert curator.run(session, run_date=TODAY) == []


def _plan() -> Plan:
    return Plan(
        audience="provider",
        pillar="policy_explainer",
        thesis="Discharge plans fail when nobody trains the caregiver.",
        title="Who performs the discharge plan",
        dek="For discharge planners.",
        meta_description="Why discharge instructions fail.",
        outline=["What changed", "What it means"],
        keywords=["caregiver training"],
        evidence=[
            Evidence(
                claim="Caregiver training is payable under 97550.",
                url="https://www.cms.gov/rule",
                title="CY 2025 PFS",
                kind="regulatory",
            ),
            Evidence(
                claim="Alverna coordinates scheduling with caregivers.",
                url="https://alvernahealth.com/providers",
                title="Providers",
                kind="product",
            ),
        ],
    )


def test_compose_appends_disclaimer_and_sources_but_not_product_links() -> None:
    body = writer.compose(_plan(), "Body text.", ["One takeaway"], [{"q": "Q?", "a": "A."}])
    assert settings.care_disclaimer in body
    assert "## Key takeaways" in body
    assert "https://www.cms.gov/rule" in body
    assert "alvernahealth.com/providers" not in body  # product facts are not citations


def make_article(session: Session, **overrides) -> Article:
    defaults = {
        "run_date": TODAY,
        "pillar": "policy_explainer",
        "audience": "provider",
        "thesis": "Discharge plans fail when nobody trains the caregiver at home.",
        "title": "Who performs the discharge plan",
        "slug": "who-performs-the-discharge-plan",
        "dek": "For discharge planners.",
        "meta_description": "Why discharge instructions fail.",
        "body_md": "Body.\n\n## Key takeaways\n\n- Train the caregiver before discharge.\n",
        "word_count": 900,
        "sources": [{"url": "https://www.cms.gov/rule", "kind": "regulatory"}],
        "evidence": ["Caregiver training is payable under 97550."],
        "status": "ready_for_review",
    }
    defaults.update(overrides)
    article = Article(**defaults)
    session.add(article)
    session.flush()
    return article


def test_promos_link_to_the_article_and_stay_in_the_care_stream(session: Session) -> None:
    article = make_article(session)
    drafts = promoter.run(session, _StubLLM(), [article])
    assert drafts
    for draft in drafts:
        assert draft.stream == STREAM_CARE
        assert draft.article_id == article.id
        assert article.slug in draft.link_reply
        assert draft.status == "ready_for_review"


def test_promo_check_blocks_unsafe_copy() -> None:
    over_limit = Draft(body="x" * 400, features={"channel": "x"}, variant=0)
    assert promoter.check(over_limit)
    promise = Draft(
        body="Your caregiver training will be reimbursed by Medicare.",
        features={"channel": "x"},
        variant=0,
    )
    assert any("promises" in note for note in promoter.check(promise))
    hashtag = Draft(body="Train caregivers #medicare", features={"channel": "x"}, variant=0)
    assert any("hashtag" in note for note in promoter.check(hashtag))


@pytest.mark.parametrize(
    "body",
    [
        "Medicare will pay you for every session you document.",
        "Medicare reimburses these sessions in full.",
        "Providers can be sure these visits are paid by Medicare.",
    ],
)
def test_promo_check_blocks_promises_without_an_auxiliary_verb(body: str) -> None:
    draft = Draft(body=body, features={"channel": "x"}, variant=0)
    assert any("promises" in note for note in promoter.check(draft))


def test_promo_check_allows_hedged_copy() -> None:
    draft = Draft(
        body="Caregiver training may be billable when it is tied to the treatment plan.",
        features={"channel": "x"},
        variant=0,
    )
    assert promoter.check(draft) == []


def test_export_writes_frontmatter(session: Session, tmp_path) -> None:
    article = make_article(session)
    paths = export.run([article], directory=tmp_path)
    text = paths[0].read_text()
    assert text.startswith("---")
    assert 'title: "Who performs the discharge plan"' in text
    assert 'audience: "provider"' in text


def test_dashboard_separates_the_two_streams(session: Session) -> None:
    article = make_article(session)
    session.add(Draft(stream=STREAM_ML, body="ml post", variant=0, status="ready_for_review"))
    session.add(
        Draft(stream=STREAM_CARE, body="care post", variant=0, status="blocked", article=article)
    )
    session.flush()

    board = dashboard.build(session)
    summaries = {s.stream: s for s in board.streams}
    assert summaries[STREAM_ML].ready == 1 and summaries[STREAM_ML].blocked == 0
    assert summaries[STREAM_CARE].ready == 0 and summaries[STREAM_CARE].blocked == 1
    assert [a.slug for a in board.articles] == [article.slug]
    assert {p.stream for p in board.posts} == {STREAM_ML, STREAM_CARE}
    assert "<html" in dashboard.render(board).lower()


class _StubLLM:
    """Stands in for the model so promo assembly is tested, not generation."""

    def complete_json(self, prompt: str, **kwargs) -> dict:
        return {
            "x_posts": [
                {"body": "Medicare added caregiver training codes; most discharges ignore them."},
                {"body": "Discharge plans assume a trained caregiver. Usually there isn't one."},
            ],
            "linkedin": "Discharge planning quietly assumes someone at home was trained.",
        }
