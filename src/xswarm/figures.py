"""Pull a real figure out of the paper the post is about.

A diagram a model invents from an abstract looks plausible and explains nothing; the
authors already drew the picture that explains their own work. When a paper has an HTML
rendering on arXiv, the figure that carries the method is in it, so take that one and
attach it. When it doesn't, the post ships without an image rather than with a decorative
one.
"""

from __future__ import annotations

import base64
import logging
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
HF_PAPER_RE = re.compile(r"huggingface\.co/papers/(\d{4}\.\d{4,5})", re.I)
REPO_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)", re.I)
MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")

FIGURE_RE = re.compile(r"<figure\b[^>]*>(.*?)</figure>", re.I | re.S)
IMG_RE = re.compile(r"<img\b[^>]*?src=\"([^\"]+)\"", re.I)
CAPTION_RE = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
NUMBERED_RE = re.compile(r"^\s*figure\s*\d+", re.I)

# What a reader needs to see: the picture of how the thing works, then the picture of
# how well it works. A teaser image or a training curve explains neither.
PREFERRED = ("overview", "architecture", "framework", "pipeline", "our method", "illustration")
RASTER = {".png", ".jpg", ".jpeg"}
MIN_WIDTH = 500
MIN_HEIGHT = 280


@dataclass
class Figure:
    path: Path
    caption: str
    source_url: str


def paper_id(url: str) -> str | None:
    """The arXiv id behind a link, including links that reach it through Hugging Face."""
    for pattern in (ARXIV_RE, HF_PAPER_RE):
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return None


def _text(html: str) -> str:
    return " ".join(TAG_RE.sub(" ", html).split())


def _score(caption: str) -> tuple[int, int]:
    """Prefer a numbered figure, and among those the one that draws the method."""
    lowered = caption.lower()
    return (
        1 if NUMBERED_RE.match(caption) else 0,
        1 if any(word in lowered for word in PREFERRED) else 0,
    )


def _dimensions(data: bytes) -> tuple[int, int] | None:
    """Enough of PNG and JPEG to reject icons and logos without pulling in Pillow."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            length = int.from_bytes(data[offset + 2 : offset + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                return width, height
            offset += 2 + length
    return None


def candidates(html: str, base_url: str) -> list[tuple[str, str]]:
    """Every figure in the paper as (image url, caption), best first."""
    found: list[tuple[tuple[int, int], str, str]] = []
    for index, block in enumerate(FIGURE_RE.finditer(html)):
        body = block.group(1)
        image = IMG_RE.search(body)
        caption_match = CAPTION_RE.search(body)
        if not image:
            continue
        caption = _text(caption_match.group(1)) if caption_match else ""
        url = urljoin(base_url, image.group(1))
        if Path(url.split("?")[0]).suffix.lower() not in RASTER:
            continue
        # Ties keep document order, so "Figure 1" wins over a later overview.
        found.append(((*_score(caption), -index), url, caption))
    found.sort(key=lambda row: row[0], reverse=True)
    return [(url, caption) for _, url, caption in found]


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        response = client.get(url, follow_redirects=True, timeout=30)
    except httpx.HTTPError as error:
        log.info("figure fetch failed for %s: %s", url, error)
        return None
    return response if response.status_code == 200 else None


def _save(client: httpx.Client, image_url: str, dest: Path) -> Path | None:
    """The image at `image_url`, saved next to `dest`, unless it is icon-sized."""
    image = _get(client, image_url)
    if image is None:
        return None
    size = _dimensions(image.content)
    if not size or size[0] < MIN_WIDTH or size[1] < MIN_HEIGHT:
        return None
    dest = dest.with_suffix(Path(image_url.split("?")[0]).suffix.lower())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image.content)
    return dest


def _readme(client: httpx.Client, owner: str, repo: str) -> str | None:
    response = _get(client, f"https://api.github.com/repos/{owner}/{repo}/readme")
    if response is None:
        return None
    try:
        return base64.b64decode(response.json().get("content", "")).decode("utf-8", "replace")
    except (ValueError, KeyError):
        return None


def repo_figure(url: str, dest: Path, *, client: httpx.Client) -> Figure | None:
    """A project's own diagram, taken from its README.

    A repo has no paper to pull a figure from, but the maintainers usually drew the
    architecture themselves and put it at the top of the README. A README that only links
    a paper is followed to that paper instead; one with neither leaves the post text-only.
    """
    match = REPO_RE.search(url or "")
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    readme = _readme(client, owner, repo)
    if not readme:
        return None
    for alt, image_url in MD_IMAGE_RE.findall(readme):
        absolute = image_url
        if not image_url.startswith("http"):
            absolute = (
                f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{image_url.lstrip('./')}"
            )
        if Path(absolute.split("?")[0]).suffix.lower() not in RASTER:
            continue
        saved = _save(client, absolute, dest)
        if saved is not None:
            caption = alt.strip() or f"Diagram from the {owner}/{repo} README"
            return Figure(path=saved, caption=caption, source_url=absolute)
    # The README's own paper is the next best thing the authors drew.
    paper = paper_id(readme)
    if paper:
        return fetch(f"https://arxiv.org/abs/{paper}", dest, client=client)
    return None


def fetch(url: str, dest: Path, *, client: httpx.Client | None = None) -> Figure | None:
    """The best figure the authors themselves drew, saved to `dest`, or None."""
    identifier = paper_id(url)
    owned = client is None
    client = client or httpx.Client(headers={"User-Agent": "xswarm/1.0"})
    try:
        if not identifier:
            return repo_figure(url, dest, client=client)
        for page in (
            f"https://arxiv.org/html/{identifier}v1",
            f"https://ar5iv.labs.arxiv.org/html/{identifier}",
        ):
            response = _get(client, page)
            if response is None:
                continue
            # Relative image sources resolve against the document URL as a browser would:
            # arXiv serves /html/<id>v1 whose directory is /html/, and its sources start
            # with the id, so adding a trailing slash here doubles the id and 404s.
            for image_url, caption in candidates(response.text, str(response.url)):
                saved = _save(client, image_url, dest)
                if saved is None:
                    continue
                log.info("figure for %s: %s", identifier, image_url)
                return Figure(path=saved, caption=caption, source_url=image_url)
        return None
    finally:
        if owned:
            client.close()
