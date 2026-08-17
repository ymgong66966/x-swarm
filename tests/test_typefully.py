from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from xswarm.models import STREAM_CARE, STREAM_ML, STREAM_OWN
from xswarm.publishers import (
    TypefullyClient,
    TypefullyError,
    configured_social_sets,
    social_set_for,
)


def client(handler) -> TypefullyClient:
    transport = httpx.MockTransport(handler)
    return TypefullyClient(
        api_key="k",
        social_set_id="set-1",
        client=httpx.Client(transport=transport, base_url="https://api.typefully.com/v2"),
    )


def test_creates_an_x_draft_with_media_and_schedule():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"id": "d1"})

    when = dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc)
    response = client(handler).create_draft(
        ["main post", "Paper: https://arxiv.org/abs/1"],
        media_ids=["m1"],
        publish_at=when,
        plan_only=True,
    )
    assert response == {"id": "d1"}
    assert seen["url"].endswith("/social-sets/set-1/drafts")
    assert seen["auth"] == "Bearer k"
    posts = seen["json"]["platforms"]["x"]["posts"]
    assert [p["text"] for p in posts] == ["main post", "Paper: https://arxiv.org/abs/1"]
    assert posts[0]["media_ids"] == ["m1"]
    # plan_at queues without publishing; publish_at would go out unattended
    assert seen["json"]["plan_at"] == when.isoformat()
    assert "publish_at" not in seen["json"]


def test_publish_at_is_used_when_not_plan_only():
    def handler(request):
        assert "publish_at" in json.loads(request.content)
        return httpx.Response(200, json={})

    client(handler).create_draft(
        ["x"], publish_at=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc), plan_only=False
    )


def test_an_unnamed_social_set_is_refused_rather_than_guessed(monkeypatch):
    """Falling back to whichever account the workspace lists first is what put an ML
    draft on the healthcare account once."""
    from xswarm.config import settings

    monkeypatch.setattr(settings, "typefully_social_set_id", None)
    bare = TypefullyClient(api_key="k", client=httpx.Client())
    with pytest.raises(TypefullyError, match="no Typefully social set"):
        _ = bare.social_set_id


def test_streams_resolve_to_their_own_accounts(monkeypatch):
    from xswarm.config import settings

    monkeypatch.setattr(settings, "typefully_social_set_id", None)
    monkeypatch.setattr(settings, "typefully_care_social_set_id", "care-set")
    monkeypatch.setattr(settings, "typefully_ml_social_set_id", "ml-set")

    assert social_set_for(STREAM_CARE) == "care-set"
    assert social_set_for(STREAM_ML) == "ml-set"
    # Your own material goes out under your own name.
    assert social_set_for(STREAM_OWN) == "ml-set"
    assert configured_social_sets() == ["care-set", "ml-set"]


def test_the_pre_two_account_setting_still_names_the_care_account(monkeypatch):
    from xswarm.config import settings

    monkeypatch.setattr(settings, "typefully_social_set_id", "326727")
    monkeypatch.setattr(settings, "typefully_care_social_set_id", None)
    monkeypatch.setattr(settings, "typefully_ml_social_set_id", None)

    assert social_set_for(STREAM_CARE) == "326727"
    assert configured_social_sets() == ["326727"]
    with pytest.raises(TypefullyError):
        social_set_for(STREAM_ML)


def test_analytics_follows_pagination():
    pages = [
        {"results": [{"post_id": "1"}, {"post_id": "2"}], "next": "more"},
        {"results": [{"post_id": "3"}], "next": None},
    ]
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=pages[len(calls) - 1])

    rows = client(handler).analytics_posts(dt.date(2026, 8, 1), dt.date(2026, 8, 14), limit=2)
    assert [r["post_id"] for r in rows] == ["1", "2", "3"]
    assert calls[1]["offset"] == "2"


def test_http_error_is_wrapped():
    with pytest.raises(TypefullyError):
        client(lambda request: httpx.Response(401, text="nope")).create_draft(["x"])


def test_missing_key_refuses_to_construct(monkeypatch):
    from xswarm.config import settings

    monkeypatch.setattr(settings, "typefully_api_key", None)
    with pytest.raises(TypefullyError):
        TypefullyClient()


def test_updates_a_queued_draft_in_place():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "d1"})

    when = dt.datetime(2026, 8, 20, 12, 30, tzinfo=dt.timezone.utc)
    client(handler).update_draft(
        "d1", ["main post", "Full piece: https://a.test/x"], media_ids=["m1"], publish_at=when,
        plan_only=True,
    )
    assert seen["method"] == "PATCH"
    assert seen["url"].endswith("/social-sets/set-1/drafts/d1")
    assert seen["json"]["platforms"]["x"]["posts"][0]["media_ids"] == ["m1"]
    assert seen["json"]["plan_at"] == when.isoformat()
