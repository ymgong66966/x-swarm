from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from xswarm.care import editor
from xswarm.config import settings
from xswarm.models import Article

CMS = "https://www.cms.gov/rule"
KFF = "https://www.kff.org/report"
REDDIT = "https://www.reddit.com/r/CaregiverSupport/comments/x"

BODY = (
    "Discharge instructions assume somebody at home can carry them out.\n\n"
    "## What changed\n"
    f"Medicare pays for caregiver training under [codes 97550 and G0541]({CMS}).\n"
    f"Readmissions within 30 days remain [a costly failure mode]({KFF}).\n"
    "Caregivers in public forums frequently describe feeling unprepared.\n"
)


def make_article(body: str, **overrides) -> Article:
    defaults = {
        "run_date": dt.date.today(),
        "pillar": "policy_explainer",
        "audience": "provider",
        "thesis": "Discharge fails without a trained caregiver, and Medicare now pays to fix it.",
        "title": "Who performs the discharge plan",
        "slug": "who-performs-the-discharge-plan",
        "dek": "For discharge planners.",
        "meta_description": "Why discharge instructions fail and what Medicare now pays for.",
        "sources": [{"url": CMS, "kind": "regulatory"}, {"url": KFF, "kind": "press"}],
        "evidence": ["codes"],
        "word_count": settings.care_min_words,
    }
    defaults.update(overrides)
    disclaimer = f"\n\n_{settings.care_disclaimer}_\n"
    return Article(body_md=body + disclaimer, **defaults)


def test_a_sound_article_passes() -> None:
    assert editor.check(make_article(BODY)) == []


def test_missing_disclaimer_is_blocked() -> None:
    article = make_article(BODY)
    article.body_md = BODY
    assert any("disclaimer" in note for note in editor.check(article))


def test_billing_claim_needs_a_government_citation() -> None:
    body = BODY.replace(CMS, KFF)
    notes = editor.check(make_article(body, sources=[{"url": KFF, "kind": "regulatory"}]))
    assert any("government citation" in note for note in notes)


def test_uncited_statistic_is_blocked() -> None:
    body = BODY + "\nCaregiver training cuts readmissions by 30 percent.\n"
    assert any("uncited statistic" in note for note in editor.check(make_article(body)))


def test_forum_link_cannot_carry_a_regulatory_claim() -> None:
    body = BODY + f"\nMedicare reimbursement rose 12 percent [last year]({REDDIT}).\n"
    notes = editor.check(
        make_article(
            body,
            sources=[
                {"url": CMS, "kind": "regulatory"},
                {"url": KFF, "kind": "press"},
                {"url": REDDIT, "kind": "signal"},
            ],
        )
    )
    assert any("forum/social link used as evidence" in note for note in notes)


@pytest.mark.parametrize(
    "sentence",
    [
        "Your training will be reimbursed by Medicare.",
        "These sessions are always covered.",
        "Caregiver training is clinically proven to prevent falls.",
    ],
)
def test_promises_and_marketing_claims_are_blocked(sentence: str) -> None:
    assert editor.check(make_article(BODY + "\n" + sentence + "\n"))


@pytest.mark.parametrize(
    "sentence",
    [
        "You should stop the medication if the patient seems drowsy.",
        "Reduce their dose when they refuse food.",
    ],
)
def test_personalised_clinical_direction_is_blocked(sentence: str) -> None:
    notes = editor.check(make_article(BODY + "\n" + sentence + "\n"))
    assert any("clinical direction" in note for note in notes)


def test_hedged_billing_language_survives() -> None:
    body = BODY + "\nWhen documentation requirements are met, the session may be billable.\n"
    assert editor.check(make_article(body)) == []


def test_a_question_about_coverage_is_not_a_promise() -> None:
    body = BODY + "\n**What kinds of topics are covered in caregiver training?**\n"
    assert editor.check(make_article(body)) == []


@pytest.mark.parametrize(
    "sentence",
    [
        # Promises that carry no auxiliary verb.
        "Medicare reimburses every completed session in full.",
        "Medicare will pay you for every training session, if your NPI is enrolled.",
        "Providers can be sure these visits are paid by Medicare.",
        # Anecdotes that read as invented or as PHI.
        "A caregiver we worked with, Maria, 68, could not manage transfers after her stroke.",
        "Last month a family in Ohio told us their mother was readmitted twice.",
        # Direction about a medication, however it is framed.
        "Have the caregiver reduce the dose overnight if the patient seems drowsy.",
        "Stop the medication if drowsiness appears.",
        "You should give them an extra dose when the pain returns.",
        # Statistics stated without a source, including word and ratio forms.
        "Medicare pays $89 per 15-minute session.",
        "Twenty-seven of every 100 caregivers report no training at all.",
        "Roughly 1 in 3 discharges fail because nobody at home was trained.",
    ],
)
def test_unsafe_sentences_found_in_adversarial_testing_are_blocked(sentence: str) -> None:
    assert editor.check(make_article(BODY + "\n" + sentence + "\n"))


@pytest.mark.parametrize(
    "sentence",
    [
        "Alverna's provider onboarding takes about 30 minutes.",
        "A licensed clinician leads a 30-minute session.",
        f"Medicare pays for caregiver training when the plan documents it ([CMS]({CMS})).",
        "Coverage varies by plan, and copays may apply.",
    ],
)
def test_ordinary_product_and_hedged_copy_survives(sentence: str) -> None:
    assert editor.check(make_article(BODY + "\n" + sentence + "\n")) == []


def test_invented_patient_story_is_blocked() -> None:
    body = BODY + "\nOne of our patients could not manage transfers at home.\n"
    assert any("invented patient" in note for note in editor.check(make_article(body)))


def test_uncited_url_is_flagged() -> None:
    body = BODY + "\nSee [this analysis](https://example.com/whatever) for more.\n"
    notes = editor.check(make_article(body))
    assert any("not in the article's source list" in note for note in notes)


def test_review_sets_status_and_notes(session: Session) -> None:
    good = make_article(BODY)
    bad = make_article(BODY, thesis="")
    session.add_all([good, bad])
    passed = editor.review(session, [good, bad])
    assert passed == [good]
    assert good.status == "ready_for_review"
    assert bad.status == "blocked" and bad.editor_notes
