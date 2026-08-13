from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from xswarm.publishers import TypefullyClient, TypefullyError


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


def test_social_set_is_discovered_when_unset():
    def handler(request):
        assert request.url.path.endswith("/social-sets")
        return httpx.Response(200, json={"results": [{"id": "auto-1"}]})

    transport = httpx.MockTransport(handler)
    discovered = TypefullyClient(
        api_key="k",
        social_set_id=None,
        client=httpx.Client(transport=transport, base_url="https://api.typefully.com/v2"),
    )
    assert discovered.social_set_id == "auto-1"


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
