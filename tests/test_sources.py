from __future__ import annotations

from xswarm.agents.scout import _merge
from xswarm.sources import RawItem
from xswarm.sources.arxiv import _parse as parse_arxiv
from xswarm.sources.arxiv import _parse_rss as parse_arxiv_rss
from xswarm.sources.hf_papers import _parse as parse_hf

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <published>2024-01-01T00:00:00Z</published>
    <title>Speculative tool
      calling</title>
    <summary>We show 3.2x lower latency.</summary>
    <author><name>Ada Lovelace</name></author>
    <category term="cs.LG"/>
  </entry>
</feed>
"""


ARXIV_RSS = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>oai:arXiv.org:2608.11254v1</id>
    <title>FarSky: latent-space coupling</title>
    <summary>arXiv:2608.11254v1 Announce Type: new
Abstract: We improve skill by 11 points.</summary>
    <category term="cs.LG"/>
    <published>2026-08-13T00:00:00-04:00</published>
    <arxiv:announce_type>new</arxiv:announce_type>
    <dc:creator>Yann Fabel, Bijan Nouri</dc:creator>
  </entry>
  <entry>
    <id>oai:arXiv.org:2601.00002v2</id>
    <title>A cross-listed replacement</title>
    <summary>arXiv:2601.00002v2 Announce Type: replace
Abstract: Old news.</summary>
    <published>2026-08-13T00:00:00-04:00</published>
    <arxiv:announce_type>replace</arxiv:announce_type>
    <dc:creator>Someone Else</dc:creator>
  </entry>
</feed>
"""


def test_parse_arxiv_rss_keeps_new_announcements_only():
    items = parse_arxiv_rss(ARXIV_RSS)
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "2608.11254v1"
    assert item.url == "https://arxiv.org/abs/2608.11254"
    assert item.summary == "We improve skill by 11 points."
    assert item.authors == ["Yann Fabel", "Bijan Nouri"]


def test_parse_arxiv():
    items = parse_arxiv(ARXIV_XML)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Speculative tool calling"
    assert item.external_id == "2401.00001v1"
    assert item.authors == ["Ada Lovelace"]
    assert item.signals["categories"] == ["cs.LG"]


def test_parse_hf():
    items = parse_hf(
        [
            {
                "paper": {
                    "id": "2401.00001",
                    "title": "Speculative tool calling",
                    "summary": "s",
                    "upvotes": 42,
                    "authors": [{"name": "Ada Lovelace"}],
                },
                "numComments": 3,
            }
        ]
    )
    assert items[0].signals == {"hf_upvotes": 42, "hf_comments": 3}


def test_fingerprint_matches_across_sources():
    a = RawItem(
        source="arxiv",
        url="http://arxiv.org/abs/2401.00001v1",
        title="A",
        external_id="2401.00001v1",
    )
    b = RawItem(
        source="hf_daily",
        url="https://huggingface.co/papers/2401.00001",
        title="A different title",
        external_id="2401.00001",
    )
    assert a.fingerprint == b.fingerprint


def test_merge_unions_signals_and_keeps_longest_summary():
    a = RawItem(
        source="arxiv",
        url="http://arxiv.org/abs/2401.00001",
        title="A",
        summary="short",
        external_id="2401.00001",
    )
    b = RawItem(
        source="hf_daily",
        url="x",
        title="A",
        summary="a much longer summary",
        external_id="2401.00001",
        signals={"hf_upvotes": 10},
    )
    merged = _merge([a, b])
    assert len(merged) == 1
    assert merged[0].summary == "a much longer summary"
    assert merged[0].signals["hf_upvotes"] == 10
    assert merged[0].signals["also_seen_in"] == ["hf_daily"]
