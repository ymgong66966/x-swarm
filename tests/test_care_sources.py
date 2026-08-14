from __future__ import annotations

import json

import httpx

from xswarm.analytics import crawl
from xswarm.care import sources
from xswarm.models import STREAM_CARE

FR_PAYLOAD = {
    "results": [
        {
            "title": "Medicare Program; CY 2025 Physician Fee Schedule",
            "abstract": "Adds caregiver training services to the telehealth list.",
            "html_url": "https://www.federalregister.gov/documents/2024/1",
            "publication_date": "2024-11-01",
            "document_number": "2024-1",
            "type": "Rule",
        }
    ]
}

REDDIT_RSS = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Exhausted caring for my mother with dementia</title>
    <link href="https://www.reddit.com/r/CaregiverSupport/comments/x"/>
    <content type="html">My mother lives at home and I am the only one helping.</content>
    <published>2026-08-01T00:00:00Z</published>
  </entry>
</feed>
"""


def test_federal_register_items_are_regulatory_with_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(FR_PAYLOAD))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        items = sources.federal_register(client)

    assert items
    item = items[0]
    assert item.signals["evidence_kind"] == "regulatory"
    assert item.url.startswith("https://www.federalregister.gov/")
    assert item.external_id == "2024-1"


def test_forum_items_are_signal_only_and_carry_no_thread_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=REDDIT_RSS)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        items = sources.forums(client)

    assert items
    for item in items:
        assert item.signals["evidence_kind"] == "signal"
        assert item.summary == ""  # thread bodies are never stored


def test_a_failing_source_does_not_take_the_run_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sources.federal_register(client) == []
        assert sources.forums(client) == []


PAGE = """<html><head>
<title>Who performs the discharge plan</title>
<meta name="description" content="Why discharge instructions fail and what Medicare pays for now.">
<link rel="canonical" href="https://alvernahealth.com/blog/discharge">
</head><body>text</body></html>"""

NOINDEX = '<html><head><title>t</title><meta name="robots" content="noindex"></head></html>'


def test_crawl_flags_a_noindex_unlisted_page(session) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(404)
        return httpx.Response(200, text=NOINDEX, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        checks = crawl.run(
            session, urls=["https://alvernahealth.com/blog/discharge"], client=client
        )

    check = checks[0]
    assert check.stream == STREAM_CARE
    assert check.indexable is False
    assert any("noindex" in issue for issue in check.issues)
    assert any("sitemap" in issue for issue in check.issues)


def test_crawl_does_not_fetch_a_disallowed_page(session) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /providers\n")
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        check = crawl.run(
            session, urls=["https://alvernahealth.com/providers"], client=client
        )[0]

    assert "/providers" not in requested  # reporting the block must not bypass it
    assert check.robots_allowed is False
    assert check.issues == ["blocked by robots.txt"]


def test_crawl_passes_a_healthy_listed_page(session) -> None:
    sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://alvernahealth.com/blog/discharge</loc></url></urlset>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        check = crawl.run(
            session, urls=["https://alvernahealth.com/blog/discharge"], client=client
        )[0]

    assert check.in_sitemap is True
    assert check.indexable is True
    assert check.issues == []
