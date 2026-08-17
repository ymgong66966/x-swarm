from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

import pytest
from conftest import make_draft

from xswarm.agents import publisher
from xswarm.config import settings
from xswarm.models import STREAM_CARE, STREAM_ML, Asset, Publication
from xswarm.publishers import TypefullyError

ET = ZoneInfo("America/New_York")


def test_slots_skip_the_past_and_roll_into_tomorrow(monkeypatch):
    monkeypatch.setattr(settings, "publish_jitter_minutes", 0)
    now = dt.datetime(2026, 8, 20, 13, 0, tzinfo=ET)
    slots = publisher.next_slots(3, taken=[], now=now, rng=random.Random(0))
    assert [s.strftime("%m-%d %H:%M") for s in slots] == [
        "08-20 17:00",
        "08-21 08:45",
        "08-21 12:30",
    ]


def test_taken_slots_are_not_reused(monkeypatch):
    monkeypatch.setattr(settings, "publish_jitter_minutes", 0)
    now = dt.datetime(2026, 8, 20, 6, 0, tzinfo=ET)
    taken = [dt.datetime(2026, 8, 20, 8, 45, tzinfo=ET)]
    slots = publisher.next_slots(1, taken=taken, now=now, rng=random.Random(0))
    assert slots[0].strftime("%H:%M") == "12:30"


def test_jitter_stays_within_bounds(monkeypatch):
    monkeypatch.setattr(settings, "publish_jitter_minutes", 7)
    now = dt.datetime(2026, 8, 20, 6, 0, tzinfo=ET)
    slot = publisher.next_slots(1, taken=[], now=now, rng=random.Random(1))[0]
    assert abs((slot - dt.datetime(2026, 8, 20, 8, 45, tzinfo=ET)).total_seconds()) <= 7 * 60


class FakeClient:
    def __init__(self):
        self.drafts = []

    def upload_media(self, path):
        return f"media-{path.name}"

    def create_draft(self, posts, **kwargs):
        self.drafts.append((posts, kwargs))
        return {"draft_id": "tf-1"}


def test_publish_persists_provider_id_and_thread(session, brief):
    draft = make_draft(brief, "the post", link_reply="Paper: https://arxiv.org/abs/1")
    draft.status = "approved"
    session.add(draft)
    session.flush()
    client = FakeClient()
    when = dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc)

    publication = publisher.publish(session, draft, when, client=client, plan_only=True)

    assert publication.provider_draft_id == "tf-1"
    assert publication.status == "planned"
    assert draft.status == "scheduled"
    posts, kwargs = client.drafts[0]
    assert posts == ["the post", "Paper: https://arxiv.org/abs/1"]
    assert kwargs["plan_only"] is True


def test_a_card_link_goes_in_the_post_and_replaces_the_upload(session, brief, tmp_path):
    """X shows either an uploaded image or the link's preview card, never both, and the
    card is the one that carries the headline and is clickable."""
    image = tmp_path / "hero.jpg"
    image.write_bytes(b"jpeg")
    draft = make_draft(brief, "the post", link_reply="Providers: https://a.test/providers")
    draft.card_url = "https://a.test/x?utm_source=x"
    draft.assets.append(Asset(kind="hero", path=str(image)))
    draft.status = "approved"
    session.add(draft)
    session.flush()

    client = FakeClient()
    publisher.publish(
        session, draft, dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc), client=client
    )

    posts, kwargs = client.drafts[0]
    assert posts[0] == "the post\n\nhttps://a.test/x?utm_source=x"
    assert posts[1] == "Providers: https://a.test/providers"
    assert kwargs["media_ids"] == []


def test_dry_run_records_intent_without_calling_the_api(session, brief):
    draft = make_draft(brief, "the post")
    draft.status = "approved"
    session.add(draft)
    session.flush()
    when = dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc)

    publication = publisher.publish(session, draft, when, client=None)

    assert publication.status == "pending"
    assert publication.provider_draft_id is None
    assert draft.status == "approved"


def test_a_dry_run_does_not_take_the_draft_out_of_later_real_runs(session, brief, monkeypatch):
    """A dry run leaves a Publication with no provider id. Treating that as sent stranded
    every draft rehearsed before the API key arrived."""
    monkeypatch.setattr(settings, "typefully_api_key", "key")
    draft = make_draft(brief, "the post")
    draft.status = "approved"
    session.add(draft)
    session.flush()
    publisher.publish(session, draft, dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc))
    session.flush()

    client = FakeClient()
    monkeypatch.setattr(settings, "typefully_ml_social_set_id", "ml-set")
    monkeypatch.setattr(publisher, "TypefullyClient", lambda **kwargs: client)
    publications = publisher.run(session)

    assert [p.draft_id for p in publications] == [draft.id]
    assert publications[0].provider_draft_id == "tf-1"


def test_run_only_schedules_approved_drafts(session, brief, monkeypatch):
    monkeypatch.setattr(settings, "typefully_api_key", None)
    approved = make_draft(brief, "approved post")
    approved.status = "approved"
    waiting = make_draft(brief, "waiting post")
    waiting.variant = 1
    waiting.status = "ready_for_review"
    session.add_all([approved, waiting])
    session.flush()

    publications = publisher.run(session, dry_run=True)

    assert [p.draft_id for p in publications] == [approved.id]


def test_run_skips_drafts_already_scheduled(session, brief, monkeypatch):
    monkeypatch.setattr(settings, "typefully_api_key", None)
    draft = make_draft(brief, "approved post")
    draft.status = "approved"
    session.add(draft)
    session.flush()
    session.add(Publication(draft_id=draft.id, status="planned"))
    session.flush()

    assert publisher.run(session, dry_run=True) == []


class FakeUpdateClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.updates = []

    def update_draft(self, draft_id, posts, **kwargs):
        self.updates.append((draft_id, posts, kwargs))
        return {"id": draft_id}


def test_resend_rewrites_the_queued_copy_without_moving_it(session, brief, tmp_path):
    """A promo that gained an image after it was queued has to reach the queued post,
    and it must keep its slot and provider id or the metrics stop matching."""
    image = tmp_path / "hero.jpg"
    image.write_bytes(b"jpeg")
    draft = make_draft(brief, "the post", link_reply="Full piece: https://a.test/x")
    draft.assets.append(Asset(kind="hero", path=str(image)))
    session.add(draft)
    session.flush()
    when = dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc)
    session.add(
        Publication(
            draft_id=draft.id, provider_draft_id="tf-9", scheduled_for=when, status="planned"
        )
    )
    session.flush()

    client = FakeUpdateClient()
    publisher.resend(session, draft, client=client)

    draft_id, posts, kwargs = client.updates[0]
    assert draft_id == "tf-9"
    assert posts == ["the post", "Full piece: https://a.test/x"]
    assert kwargs["media_ids"] == ["media-hero.jpg"]
    # The provider keeps its own schedule: re-sending a timezone-stripped time could move it.
    assert "publish_at" not in kwargs
    assert draft.publication.scheduled_for is not None
    assert draft.publication.provider_draft_id == "tf-9"


def test_resend_refuses_a_post_that_already_went_out(session, brief):
    draft = make_draft(brief, "the post")
    session.add(draft)
    session.flush()
    session.add(
        Publication(
            draft_id=draft.id,
            provider_draft_id="tf-9",
            status="published",
            published_at=dt.datetime.now(dt.timezone.utc),
        )
    )
    session.flush()
    with pytest.raises(ValueError, match="already published"):
        publisher.resend(session, draft, client=FakeUpdateClient())


def test_resend_refuses_a_draft_never_sent(session, brief):
    draft = make_draft(brief, "the post")
    session.add(draft)
    session.flush()
    with pytest.raises(ValueError, match="never sent"):
        publisher.resend(session, draft, client=FakeUpdateClient())


def _two_accounts(monkeypatch):
    monkeypatch.setattr(settings, "typefully_api_key", "key")
    monkeypatch.setattr(settings, "typefully_social_set_id", None)
    monkeypatch.setattr(settings, "typefully_care_social_set_id", "care-set")
    monkeypatch.setattr(settings, "typefully_ml_social_set_id", "ml-set")


def test_each_stream_posts_to_its_own_account(session, brief, monkeypatch):
    """The healthcare promos are the Alverna account's; ML posts are not."""
    _two_accounts(monkeypatch)
    built = []
    monkeypatch.setattr(
        publisher,
        "TypefullyClient",
        lambda **kwargs: built.append(kwargs["social_set_id"]) or FakeClient(),
    )
    clients = publisher.StreamClients()

    clients.get(STREAM_ML)
    clients.get(STREAM_CARE)
    clients.get(STREAM_ML)

    assert built == ["ml-set", "care-set"]


def test_an_unconfigured_account_is_refused_rather_than_sent_elsewhere(session, monkeypatch):
    monkeypatch.setattr(settings, "typefully_api_key", "key")
    monkeypatch.setattr(settings, "typefully_social_set_id", "care-set")
    monkeypatch.setattr(settings, "typefully_care_social_set_id", None)
    monkeypatch.setattr(settings, "typefully_ml_social_set_id", None)
    clients = publisher.StreamClients()

    assert clients.get(STREAM_CARE) is not None
    with pytest.raises(TypefullyError, match="ml"):
        clients.get(STREAM_ML)


def test_run_leaves_a_draft_approved_when_its_account_is_missing(session, brief, monkeypatch):
    monkeypatch.setattr(settings, "typefully_api_key", "key")
    monkeypatch.setattr(settings, "typefully_social_set_id", None)
    monkeypatch.setattr(settings, "typefully_care_social_set_id", "care-set")
    monkeypatch.setattr(settings, "typefully_ml_social_set_id", None)
    draft = make_draft(brief, "an ml post")
    draft.status = "approved"
    session.add(draft)
    session.flush()

    assert publisher.run(session) == []
    assert draft.status == "approved"
    assert draft.publication is None
