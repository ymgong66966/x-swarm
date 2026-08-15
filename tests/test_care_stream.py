"""Curation, promos and stream isolation for the care pipeline."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from xswarm.agents import publisher
from xswarm.analytics import dashboard
from xswarm.care import curator, export, promoter, scorecard, writer
from xswarm.care.angle import Evidence, Plan
from xswarm.config import settings
from xswarm.models import (
    STREAM_CARE,
    STREAM_ML,
    Article,
    CrawlCheck,
    Draft,
    Item,
    PostMetric,
    Publication,
)

TODAY = dt.date.today()

# Posts long enough to clear the length floor: a hook with one line under it is the
# shape the checks now reject, so a stub that short would test nothing real.
LONG_HOOK = (
    "Who taught the caregiver?\n\n"
    "Usually nobody. The discharge summary lists the medications and the follow-up, "
    "and assumes somebody at home knows how to do the transfers.\n\n"
    "Training tied to the treatment plan may be billable when a clinician runs it."
)
LONG_HANDOVER = (
    "Discharge day is not a teaching moment.\n\n"
    "It is a handover, and the person taking it has been awake since five with a "
    "folder in one hand and no idea what a safe transfer looks like.\n\n"
    "Plan-tied caregiver training exists to close exactly that gap."
)
LONG_LINKEDIN = (
    "Discharge planning quietly assumes someone at home was trained.\n\n"
    "Most of the time nobody was. The summary goes out, the follow-up is scheduled, and "
    "the person who will actually do the transfers, the medications and the overnight "
    "watching has been shown none of it.\n\n"
    "Caregiver training that is tied to the patient's treatment plan and delivered by a "
    "clinician may be billable, and the documentation is where teams lose it: the note "
    "has to name the goal in the plan that the session served."
)
STUB_CODES = (
    "Medicare added caregiver training codes and most discharges ignore them.\n\n"
    "The sessions may be billable when the note ties them to the patient's own goals, "
    "and few teams have changed the discharge workflow to use them.\n\n"
    "The gap is documentation."
)
STUB_ASSUMES = (
    "Discharge plans assume a trained caregiver.\n\n"
    "Usually there isn't one. The plan lands on a spouse who watched a transfer once, "
    "on the way out of the building.\n\n"
    "Training tied to that plan may be billable when a clinician runs the session."
)


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
        assert article.slug in draft.card_url
        assert draft.status == "ready_for_review"


def test_promo_links_are_tagged_and_point_at_a_page_with_a_form(session: Session) -> None:
    """A promo that only links the article sends attention nowhere it can be used, and an
    untagged link can never be attributed after the fact."""
    article = make_article(session, audience="provider", published_url="https://x.test/a/slug")
    card = promoter.card_url(article, channel="x", variant=2)
    assert "https://x.test/a/slug?" in card
    assert "utm_source=x" in card and "utm_campaign=care_article" in card
    assert f"utm_content={article.slug}-v2" in card
    reply = promoter.link_reply(article, channel="x", variant=2)
    assert "alvernahealth.com/providers?" in reply
    assert "utm_campaign=care_landing" in reply
    assert promoter.landing_url("trainer").endswith("/trainers")


def test_the_article_link_rides_in_the_post_so_a_preview_card_renders(session: Session) -> None:
    """X draws the card from the first link in the post itself. In the reply it is a bare
    URL under a post nobody has a reason to trust yet."""
    article = make_article(session, published_url="https://x.test/a/slug")
    draft = promoter.run(session, _StubLLM(), [article])[0]
    assert "https://x.test/a/slug?" in draft.card_url
    assert "http" not in draft.body
    assert "Full piece" not in draft.link_reply
    assert publisher._thread(draft)[0].endswith(draft.card_url)


def test_without_the_card_both_links_go_back_in_the_reply(session: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "care_promo_link_card", False)
    article = make_article(session, published_url="https://x.test/a/slug")
    draft = promoter.run(session, _StubLLM(), [article])[0]
    assert draft.card_url == ""
    assert "Full piece: https://x.test/a/slug?" in draft.link_reply


def test_the_card_link_is_charged_against_the_post_budget() -> None:
    """t.co spends 23 characters on the link whatever its length, and X counts them."""
    assert promoter.x_budget() == settings.max_post_chars - promoter.LINK_COST


def test_releasing_an_article_gives_every_promo_its_photograph(
    session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "care_promo_link_card", False)
    hero = tmp_path / "hero.jpg"
    hero.write_bytes(b"jpeg")
    article = make_article(
        session,
        published_url="https://x.test/a/slug",
        hero_path=str(hero),
        hero_alt="A nurse showing a transfer grip.",
    )
    promoter.run(session, _StubLLM(), [article])

    assert promoter.release(session, article) == len(article.promos)
    for draft in article.promos:
        assert draft.status == "approved"
        assert [asset.path for asset in draft.assets] == [str(hero)]
        assert draft.assets[0].alt_text == "A nurse showing a transfer grip."
        assert "utm_source" in draft.link_reply

    # Running it twice must not stack duplicate uploads onto the same post.
    promoter.release(session, article)
    assert all(len(draft.assets) == 1 for draft in article.promos)


def test_the_card_supplies_the_photograph_instead_of_an_upload(session: Session, tmp_path) -> None:
    """An uploaded image replaces the preview card on X, and the card is the version that
    carries the headline and is clickable, so the hero arrives as og:image instead."""
    hero = tmp_path / "hero.jpg"
    hero.write_bytes(b"jpeg")
    article = make_article(
        session, published_url="https://x.test/a/slug", hero_path=str(hero), hero_alt="A grip."
    )
    promoter.run(session, _StubLLM(), [article])
    assert promoter.attach_hero(session, article) == 0
    assert all(draft.assets == [] for draft in article.promos)


def test_a_missing_hero_file_is_not_attached(session: Session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "care_promo_link_card", False)
    article = make_article(session, hero_path=str(tmp_path / "gone.jpg"))
    promoter.run(session, _StubLLM(), [article])
    assert promoter.attach_hero(session, article) == 0


def test_scorecard_pairs_crawl_state_with_what_x_sent(session: Session) -> None:
    article = make_article(
        session, published_url="https://alvernahealth.com/resources/slug", status="published"
    )
    promoter.run(session, _StubLLM(), [article])
    session.add(
        CrawlCheck(
            url=article.published_url,
            status_code=200,
            in_sitemap=True,
            indexable=True,
            issues=["title is 71 characters"],
        )
    )
    publication = Publication(draft_id=article.promos[0].id, post_url="https://x.com/p/1")
    session.add(publication)
    session.flush()
    session.add(PostMetric(publication_id=publication.id, impressions=400, link_clicks=8))
    session.flush()

    row = next(r for r in scorecard.build(session) if r.article_id == article.id)
    assert row.indexable is True and row.in_sitemap is True
    assert row.issues == ["title is 71 characters"]
    assert row.impressions == 400 and row.link_clicks == 8
    assert row.click_rate == 0.02
    assert row.scheduled == 1 and row.promos == len(article.promos)


def test_scorecard_reports_unknown_rather_than_healthy_for_an_unchecked_url(
    session: Session,
) -> None:
    """A never-crawled article must not read as indexable — that is the failure the
    scorecard exists to catch."""
    article = make_article(session)
    row = next(r for r in scorecard.build(session) if r.article_id == article.id)
    assert row.indexable is None and row.in_sitemap is None
    assert row.click_rate == 0.0


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
        body="Who taught the caregiver?\n\nCaregiver training may be billable when it is "
        "tied to the treatment plan and a clinician runs the session. The documentation "
        "is the part teams miss: the note has to name the patient goal it serves.",
        features={"channel": "x"},
        variant=0,
    )
    assert promoter.check(draft) == []


def test_promo_check_blocks_a_call_to_action_and_marketing_filler() -> None:
    """These read as safe and sourced and still sound like an ad; the link is the CTA."""
    cta = Draft(
        body="Training may be billable when tied to the plan.\nAsk your provider about "
        "eligibility during discharge planning.",
        features={"channel": "x"},
        variant=0,
    )
    assert any("call to action" in note for note in promoter.check(cta))
    filler = Draft(
        body="Plan-aligned training solutions empower discharge teams.",
        features={"channel": "x"},
        variant=0,
    )
    assert any("marketing filler" in note for note in promoter.check(filler))


def test_promo_check_allows_a_hook_and_keeps_its_line_breaks() -> None:
    draft = Draft(
        body="Who taught the caregiver?\n\nNobody, usually. Training may be billable when "
        "it is tied to the treatment plan, and the session has to be run by a clinician.\n\n"
        "That single requirement is why most discharge teams never bill for it at all.",
        features={"channel": "x"},
        variant=0,
    )
    assert promoter.check(draft) == []
    assert "\n" in promoter._clean(draft.body)


def test_promo_check_blocks_a_hook_with_nothing_under_it() -> None:
    """A one-line post reads as a teaser for the link, which is the thing we are not doing."""
    thin = Draft(
        body="Who taught the caregiver?\n\nUsually nobody.",
        features={"channel": "x"},
        variant=0,
    )
    assert any("nothing under it" in note for note in promoter.check(thin))


def test_em_dashes_are_removed_and_flagged() -> None:
    assert "—" not in promoter._clean("Training is billable — when a clinician runs it.")
    dashed = Draft(
        body=LONG_HOOK.replace("Usually nobody.", "Usually nobody — not one person."),
        features={"channel": "x"},
        variant=0,
    )
    assert any("em dash" in note for note in promoter.check(dashed))


def test_non_us_evidence_is_pushed_down_and_flagged() -> None:
    """Alverna sells into Medicare; a Taiwanese cohort is the wrong thing to lead with."""
    foreign = Draft(
        body=LONG_HOOK.replace("Usually nobody.", "A study in Taiwan found the same thing."),
        features={"channel": "x"},
        variant=0,
    )
    assert any("non-US" in note for note in promoter.check(foreign))
    assert promoter.check(Draft(body=LONG_HOOK, features={"channel": "x"}, variant=0)) == []
    ordered = promoter._us_first(
        ["A Taiwanese cohort study of caregiver burden", "CMS pays for 97550 in CY2025"]
    )
    assert ordered[0].startswith("CMS")


def test_the_curator_prefers_the_market_we_publish_into(session: Session) -> None:
    domestic = make_item(session, external_id="us", title="CMS caregiver training under Medicare")
    foreign = make_item(
        session,
        external_id="tw",
        title="Caregiver training under Medicare in Taiwan",
        url="https://pubmed.ncbi.nlm.nih.gov/1/",
    )
    assert curator.score_us_fit(domestic) > curator.score_us_fit(foreign)


def test_a_one_paragraph_post_gets_its_hook_onto_its_own_line() -> None:
    dense = (
        "Who taught the caregiver? Nobody did, usually, and the discharge plan still "
        "assumes somebody at home already knows how to do the transfers safely every day."
    )
    assert promoter._break_hook(dense).startswith("Who taught the caregiver?\n\nNobody")
    short = "Discharge day is not a teaching moment."
    assert promoter._break_hook(short) == short


def test_a_promo_that_breaks_the_voice_rules_is_generated_again(session: Session) -> None:
    class _SecondTimeLucky:
        def __init__(self) -> None:
            self.calls = 0

        def complete_json(self, prompt: str, **kwargs) -> dict:
            self.calls += 1
            if self.calls == 1:
                return {
                    "x_posts": [
                        {
                            "body": "Medicare may cover caregiver training when the plan ties "
                            "to it, which is a real change for discharge teams this year. "
                            "Ask your provider about eligibility."
                        }
                    ],
                    "linkedin": "",
                }
            return {
                "x_posts": [{"body": LONG_HOOK}],
                "linkedin": "",
            }

    article = make_article(session)
    llm = _SecondTimeLucky()
    drafts = promoter.run(session, llm, [article])
    assert llm.calls == 2
    assert [d.status for d in drafts] == ["ready_for_review"]


def test_rewriting_promos_keeps_the_rows_that_own_a_queue_slot(session: Session) -> None:
    """The new copy has to reach the post that is already scheduled, so the draft id and
    its publication must survive a rewrite."""
    article = make_article(session)
    promoter.run(session, _StubLLM(), [article])
    first = article.promos[0]
    first.status = "scheduled"
    session.add(Publication(draft_id=first.id, provider_draft_id="tf-9", status="planned"))
    session.flush()
    ids = [d.id for d in article.promos]

    class _NewVoice(_StubLLM):
        def complete_json(self, prompt: str, **kwargs) -> dict:
            return {
                "x_posts": [{"body": LONG_HOOK}, {"body": LONG_HANDOVER}],
                "linkedin": LONG_LINKEDIN,
            }

    changed = promoter.rewrite(session, article, _NewVoice())

    assert [d.id for d in article.promos] == ids
    assert [d.id for d in changed] == ids
    assert article.promos[0].body.startswith("Who taught the caregiver?")
    assert article.promos[0].status == "scheduled"  # still on the queue, new words
    assert article.promos[0].publication.provider_draft_id == "tf-9"


def test_rewriting_leaves_a_promo_that_already_went_out(session: Session) -> None:
    article = make_article(session)
    promoter.run(session, _StubLLM(), [article])
    posted = article.promos[0]
    before = posted.body
    session.add(
        Publication(
            draft_id=posted.id,
            provider_draft_id="tf-9",
            status="published",
            published_at=dt.datetime.now(dt.timezone.utc),
        )
    )
    session.flush()

    promoter.rewrite(session, article, _StubLLM())
    assert posted.body == before


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
            "x_posts": [{"body": STUB_CODES}, {"body": STUB_ASSUMES}],
            "linkedin": LONG_LINKEDIN,
        }
