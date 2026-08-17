from __future__ import annotations

import struct
import zlib

import httpx
import pytest

from xswarm import figures

HTML = """
<html><body>
<figure><img src="x1/teaser.png"><figcaption>(a) A subfigure with no number</figcaption></figure>
<figure><img src="x1/results.png"><figcaption>Figure 4: Results.</figcaption></figure>
<figure><img src="x1/method.png"><figcaption>Figure 1: Overview of our method.</figcaption></figure>
<figure><img src="x1/logo.svg"><figcaption>Figure 2: A vector drawing.</figcaption></figure>
</body></html>
"""
PAPER = "https://arxiv.org/abs/2608.13560"


def png(width: int, height: int) -> bytes:
    header = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    chunk = b"IHDR" + header
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(header))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk))
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://arxiv.org/abs/2608.13560v1", "2608.13560"),
        ("http://arxiv.org/pdf/2401.00001", "2401.00001"),
        ("https://huggingface.co/papers/2608.13489", "2608.13489"),
        ("https://github.com/someone/repo", None),
    ],
)
def test_the_paper_behind_a_link_is_recognised(url, expected):
    assert figures.paper_id(url) == expected


def test_the_method_figure_wins_over_a_teaser_and_svg_is_skipped():
    found = figures.candidates(HTML, "https://arxiv.org/html/2608.13560v1/")
    assert [url for url, _ in found] == [
        "https://arxiv.org/html/2608.13560v1/x1/method.png",
        "https://arxiv.org/html/2608.13560v1/x1/results.png",
        "https://arxiv.org/html/2608.13560v1/x1/teaser.png",
    ]


def test_a_small_image_is_not_worth_attaching(tmp_path):
    """Icons and logos pass every other check, so size is the only thing that rejects them."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=png(120, 60))
        return httpx.Response(200, text=HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert figures.fetch(PAPER, tmp_path / "f.png", client=client) is None


def test_a_paper_with_no_html_rendering_yields_nothing(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert figures.fetch(PAPER, tmp_path / "f.png", client=client) is None


def test_the_figure_is_saved_with_its_caption(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=png(1200, 700))
        return httpx.Response(200, text=HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    figure = figures.fetch(PAPER, tmp_path / "f.png", client=client)
    assert figure is not None
    assert figure.caption == "Figure 1: Overview of our method."
    assert figure.path.read_bytes()[:4] == b"\x89PNG"


def test_arxiv_relative_sources_resolve_like_a_browser(tmp_path):
    """arXiv serves /html/<id>v1 with sources that start with the id again, so the
    document's directory is /html/ and joining must not repeat the id."""
    html = (
        '<figure><img src="2608.13560v1/figures/f1.png">'
        "<figcaption>Figure 1: Overview.</figcaption></figure>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=png(1200, 700))
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    figure = figures.fetch(PAPER, tmp_path / "f.png", client=client)
    assert figure is not None
    assert figure.source_url == "https://arxiv.org/html/2608.13560v1/figures/f1.png"
