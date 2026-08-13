from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

from conftest import make_draft

from xswarm.agents import publisher
from xswarm.config import settings
from xswarm.models import Publication

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
