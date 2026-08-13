from __future__ import annotations

import datetime as dt

from conftest import make_draft

from xswarm.agents import measurer
from xswarm.models import Publication


def _publication(session, brief, body: str, **kwargs) -> Publication:
    draft = make_draft(brief, body)
    draft.variant = kwargs.pop("variant", 0)
    session.add(draft)
    session.flush()
    publication = Publication(draft_id=draft.id, **kwargs)
    session.add(publication)
    session.flush()
    return publication


def test_matches_on_provider_draft_id_and_stores_metrics(session, brief):
    publication = _publication(session, brief, "the post", provider_draft_id="tf-1")

    stored = measurer.ingest(
        session,
        [
            {
                "draft_id": "tf-1",
                "post_id": "9",
                "url": "https://x.com/i/status/9",
                "impressions": 1200,
                "likes": 40,
                "comments": 3,
                "shares": 5,
                "saves": 11,
                "profile_clicks": 7,
            }
        ],
    )

    assert stored == 1
    assert publication.post_id == "9"
    assert publication.status == "published"
    metric = publication.metrics[0]
    assert (metric.impressions, metric.replies, metric.reposts, metric.bookmarks) == (
        1200,
        3,
        5,
        11,
    )


def test_missing_metrics_default_to_zero(session, brief):
    publication = _publication(session, brief, "the post", provider_draft_id="tf-1")
    measurer.ingest(session, [{"draft_id": "tf-1", "impressions": 10}])
    assert publication.metrics[0].link_clicks == 0


def test_falls_back_to_preview_text(session, brief):
    publication = _publication(
        session, brief, "Speculative tool calls cut agent latency by a lot", provider_draft_id=None
    )
    stored = measurer.ingest(
        session, [{"post_id": "5", "preview_text": "Speculative tool calls cut agent"}]
    )
    assert stored == 1
    assert publication.post_id == "5"


def test_unmatched_rows_are_skipped(session, brief):
    _publication(session, brief, "the post", provider_draft_id="tf-1")
    assert measurer.ingest(session, [{"draft_id": "other", "preview_text": "unrelated"}]) == 0


def test_snapshots_accumulate_over_time(session, brief):
    publication = _publication(session, brief, "the post", provider_draft_id="tf-1")
    day1 = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
    measurer.ingest(session, [{"draft_id": "tf-1", "impressions": 100}], captured_at=day1)
    measurer.ingest(
        session,
        [{"draft_id": "tf-1", "impressions": 900}],
        captured_at=day1 + dt.timedelta(days=1),
    )
    assert [m.impressions for m in publication.metrics] == [100, 900]
