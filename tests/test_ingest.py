from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from xswarm.agents import publisher
from xswarm.config import settings
from xswarm.ingest import fetch
from xswarm.ingest import pipeline as ingest
from xswarm.llm import LLM, Usage
from xswarm.models import STREAM_OWN

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32

ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Speculative tool calling for LLM agents</title>
    <summary>We show 3.2x lower end-to-end latency on the AgentBench subset.</summary>
    <author><name>A. Researcher</name></author>
  </entry>
</feed>
"""

BLOG_HTML = """
<html><head><title>Site name — My post</title></head>
<body>
<nav><p>this navigation paragraph should be dropped entirely</p></nav>
<script>var noise = "this script text must never reach the material";</script>
<h1>Cutting inference cost by 40%</h1>
<p>We moved the KV cache off the hot path and measured a 40% drop in cost per token.</p>
<li>Short</li>
<p>The tradeoff is a 12ms increase in time to first token on cold requests.</p>
</body></html>
"""


class FakeLLM(LLM):
    def __init__(self, payload=None, *, image: bytes | None = None):
        self.payload = payload
        self._image = image
        self.dry_run = False
        self.provider = "fake"
        self.usage: list[Usage] = []

    def complete_json(self, prompt, **kwargs):
        self.last_prompt = prompt
        return self.payload

    def image(self, prompt, *, agent="illustrator", model="", quality=""):
        return self._image


# --------------------------------------------------------------------------- fetch


def test_plain_text_keeps_its_first_line_as_the_title():
    material = fetch.load("Why batching breaks agents\n\nBecause the tail dominates.")
    assert material.kind == "text"
    assert material.title == "Why batching breaks agents"
    assert "tail dominates" in material.text


def test_file_source_is_read_from_disk(tmp_path):
    path = tmp_path / "my-note.md"
    path.write_text("# Note\n\nA paragraph.")
    material = fetch.load(str(path))
    assert material.title == "Note"
    assert "A paragraph." in material.text


@respx.mock
def test_arxiv_link_uses_the_api_and_keeps_provenance():
    respx.get(fetch.ARXIV_API).mock(return_value=httpx.Response(200, text=ARXIV_ATOM))
    material = fetch.load("https://arxiv.org/abs/2401.00001")
    assert material.kind == "paper"
    assert material.url == "https://arxiv.org/abs/2401.00001"
    assert material.authors == ["A. Researcher"]
    assert "3.2x lower end-to-end latency" in material.text


@respx.mock
def test_bare_arxiv_id_resolves_to_the_same_paper():
    respx.get(fetch.ARXIV_API).mock(return_value=httpx.Response(200, text=ARXIV_ATOM))
    assert fetch.load("2401.00001").url == "https://arxiv.org/abs/2401.00001"


@respx.mock
def test_blog_extraction_drops_chrome_and_prefers_the_h1():
    url = "https://example.com/posts/inference"
    respx.get(url).mock(return_value=httpx.Response(200, text=BLOG_HTML))
    material = fetch.load(url)
    assert material.title == "Cutting inference cost by 40%"
    assert "40% drop in cost per token" in material.text
    assert "navigation paragraph" not in material.text
    assert "noise" not in material.text
    assert material.url == url


# ------------------------------------------------------------------------ pipeline


def _material() -> fetch.Material:
    return fetch.Material(
        title="Cutting inference cost by 40%",
        text=(
            "We moved the KV cache off the hot path and measured a 40% drop in cost "
            "per token. The tradeoff is a 12ms increase in time to first token."
        ),
        url="https://example.com/posts/inference",
        kind="article",
    )


def test_ingest_builds_an_own_stream_draft_with_provenance(session):
    llm = FakeLLM(
        {
            "posts": [
                "Moving the KV cache off the hot path cut cost per token by 40%.",
                "It costs 12ms more time to first token on cold requests.",
            ],
            "link_reply": "Write-up:",
            "claims": ["40% drop in cost per token"],
        }
    )
    draft = ingest.run(session, _material(), llm, illustrate_it=False)
    assert draft.stream == STREAM_OWN
    assert draft.features["source_url"] == "https://example.com/posts/inference"
    assert draft.features["pillar"] == "own_work"
    assert draft.thread == ["It costs 12ms more time to first token on cold requests."]
    assert draft.link_reply.endswith("https://example.com/posts/inference")


def test_the_editor_blocks_a_number_the_material_never_stated(session):
    llm = FakeLLM({"posts": ["This cut cost per token by 80%."], "claims": []})
    draft = ingest.run(session, _material(), llm, illustrate_it=False)
    assert draft.status == "blocked"
    assert any("80%" in note for note in draft.editor_notes)


def test_urls_stay_out_of_the_posts_themselves(session):
    llm = FakeLLM(
        {
            "posts": ["Cost per token fell 40% after the KV cache moved off the hot path."],
            "link_reply": "",
        }
    )
    draft = ingest.run(session, _material(), llm, illustrate_it=False)
    assert "http" not in draft.body
    assert draft.link_reply == "https://example.com/posts/inference"


def test_without_a_model_the_material_is_chunked_verbatim(session):
    draft = ingest.run(session, _material(), LLM(dry_run=True), illustrate_it=False)
    assert draft.body
    assert all(len(post) <= settings.max_post_chars for post in [draft.body, *draft.thread])
    assert draft.body in _material().text


def test_your_own_image_is_copied_and_used(session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assets_dir", tmp_path / "assets")
    source = tmp_path / "diagram.png"
    source.write_bytes(PNG)
    llm = FakeLLM({"posts": ["Cost per token fell 40%."]}, image=PNG)
    draft = ingest.run(session, _material(), llm, images=[source], alt="A cache diagram.")
    assets = draft.assets
    assert [a.kind for a in assets] == ["user_image"]
    assert assets[0].path != str(source)
    assert draft.alt_text == "A cache diagram."
    assert draft.features["visual_hint"] == "user_image"


def test_generated_art_is_attached_when_you_supply_no_image(session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assets_dir", tmp_path / "assets")
    llm = FakeLLM(
        {
            "posts": ["Cost per token fell 40%."],
            "style": "concept_hero",
            "subject": "A valve on a dark pipe",
            "alt_text": "A valve on a dark pipe.",
        },
        image=PNG,
    )
    draft = ingest.run(session, _material(), llm)
    assert [a.kind for a in draft.assets] == ["generated_art"]
    assert draft.features["visual_hint"] == "generated_art"


# ----------------------------------------------------------------------- scheduling


def test_scheduling_refuses_a_draft_no_human_approved(session):
    draft = ingest.run(session, _material(), LLM(dry_run=True), illustrate_it=False)
    draft.status = "ready_for_review"
    with pytest.raises(ValueError, match="not approved"):
        ingest.schedule(session, draft)


def test_dry_run_scheduling_never_calls_typefully(session, monkeypatch):
    monkeypatch.setattr(settings, "typefully_api_key", "not-used-in-dry-run")
    draft = ingest.run(session, _material(), LLM(dry_run=True), illustrate_it=False)
    draft.status = "approved"
    when = dt.datetime(2030, 1, 1, 12, tzinfo=dt.timezone.utc)
    publication = ingest.schedule(session, draft, when, dry_run=True)
    assert publication.status == "pending"
    assert publication.scheduled_for == when
    assert draft.status == "approved"


# ------------------------------------------------------------------ source robustness


def test_a_path_that_does_not_exist_is_an_error_not_a_post():
    with pytest.raises(fetch.IngestError, match="no such file"):
        fetch.load("/tmp/does-not-exist.md")


def test_a_malformed_arxiv_id_is_rejected():
    with pytest.raises(fetch.IngestError, match="not a valid arXiv id"):
        fetch.load("arxiv:2401.9")


@respx.mock
def test_an_arxiv_id_with_no_paper_behind_it_is_an_error():
    respx.get(fetch.ARXIV_API).mock(
        return_value=httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    )
    with pytest.raises(fetch.IngestError, match="no paper"):
        fetch.load("2401.00001")


@respx.mock
def test_http_errors_surface_as_one_line_not_a_traceback():
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))
    with pytest.raises(fetch.IngestError, match="HTTP 404"):
        fetch.load("https://example.com/gone")


@respx.mock
def test_a_pdf_is_refused_rather_than_stripped_into_noise():
    url = "https://example.com/paper.pdf"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
        )
    )
    with pytest.raises(fetch.IngestError, match="not a page I can read"):
        fetch.load(url)


def test_prose_is_still_prose_even_when_it_mentions_a_file():
    material = fetch.load("I rewrote loader.py today and it now streams shards.")
    assert material.kind == "text"


def test_a_missing_or_non_image_attachment_is_refused(tmp_path):
    with pytest.raises(fetch.IngestError, match="no such image"):
        ingest.check_image(tmp_path / "nope.png")
    fake = tmp_path / "notanimage.txt"
    fake.write_text("this is not a picture")
    with pytest.raises(fetch.IngestError, match="not a PNG"):
        ingest.check_image(fake)


def test_scheduling_a_second_draft_does_not_trip_over_stored_times(session):
    """SQLite returns naive datetimes; the slot arithmetic is timezone-aware."""
    first, second = (
        ingest.run(session, _material(), LLM(dry_run=True), illustrate_it=False) for _ in range(2)
    )
    times = []
    for draft in (first, second):
        draft.status = "approved"
        times.append(ingest.schedule(session, draft, dry_run=True).scheduled_for)
    assert times[0] != times[1]


def test_a_riff_container_that_is_not_webp_is_refused(tmp_path):
    """WebP is RIFF, but so is WAV; the format tag at byte 8 is what matters."""
    wav = tmp_path / "sound.bin"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    with pytest.raises(fetch.IngestError, match="not a PNG"):
        ingest.check_image(wav)
    webp = tmp_path / "pic.webp"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    ingest.check_image(webp)


def test_queued_times_are_read_back_in_the_publishing_timezone(session):
    """SQLite stores wall time; reading it as UTC would put slot spacing hours out."""
    draft = ingest.run(session, _material(), LLM(dry_run=True), illustrate_it=False)
    draft.status = "approved"
    when = ingest.schedule(session, draft, dry_run=True).scheduled_for
    session.flush()
    queued = publisher.queued_times(session)
    assert queued and all(t.utcoffset() == when.utcoffset() for t in queued)
    assert queued[0].replace(tzinfo=None) == when.replace(tzinfo=None)
