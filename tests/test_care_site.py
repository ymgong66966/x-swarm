from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from xswarm.care import site

HOME = """
<html><head><title>Alverna</title><style>.a{color:red}</style></head><body>
<h2>How it works</h2>
<p>A licensed clinician leads a 30-minute session tailored to the caregiver and patient.</p>
<p>short</p>
<li>Sessions are delivered by telehealth and documented back to the referring provider.</li>
<script>console.log("Sessions are fake and should never be stored as a fact.")</script>
</body></html>
"""


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_keeps_statements_and_drops_chrome() -> None:
    facts = site.extract(HOME, "https://alvernahealth.com/")
    texts = [text for _, text in facts]
    assert any(text.startswith("A licensed clinician") for text in texts)
    assert all("console.log" not in text for text in texts)
    assert "short" not in texts  # below the minimum length
    assert facts[0][0] == "How it works"  # carries its heading


def test_robots_disallow_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /providers\n")
        return httpx.Response(200, text=HOME)

    with _client(handler) as client:
        assert site.robots_allows("https://alvernahealth.com/", client) is True
        assert site.robots_allows("https://alvernahealth.com/providers", client) is False


def test_missing_robots_is_not_a_disallow() -> None:
    with _client(lambda request: httpx.Response(404)) as client:
        assert site.robots_allows("https://alvernahealth.com/", client) is True


@pytest.mark.parametrize("run_twice", [False, True])
def test_sync_stores_facts_once(session: Session, run_twice: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=HOME)

    with _client(handler) as client:
        first = site.sync(session, client=client)
        second = site.sync(session, client=client) if run_twice else []

    assert first
    assert second == []  # fingerprinted, so a re-read adds nothing


def test_sync_skips_disallowed_pages(session: Session) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError("must not fetch a disallowed page")

    with _client(handler) as client:
        assert site.sync(session, client=client) == []


def test_context_puts_the_audiences_own_facts_first(session: Session) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(200, text=HOME)

    with _client(handler) as client:
        site.sync(session, client=client)

    facts = site.context(session, "provider")
    assert facts
    assert [fact.audience for fact in facts] == sorted(
        (fact.audience for fact in facts), key=lambda a: 0 if a == "provider" else 1
    )
