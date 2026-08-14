"""Turn whatever you hand the pipeline into plain text with provenance.

Three inputs are supported because those are the three you actually have: a link
(arXiv, your blog, anyone's post), a local file, or text pasted straight in. Everything
downstream sees the same `Material`, so the thread writer does not care which it was.

HTML is extracted with the same block-level regex approach as the care site reader
rather than a parser dependency: we only ever want headings, paragraphs and list items.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import feedparser
import httpx

from ..config import settings

log = logging.getLogger(__name__)

TIMEOUT = 20.0
USER_AGENT = "x-swarm/1.0 (+own-material-ingest)"
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
ARXIV_ID = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})$", re.IGNORECASE)
ARXIV_API = "https://export.arxiv.org/api/query"

_SCRIPT = re.compile(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", re.DOTALL | re.I)
_BLOCK = re.compile(r"<(h1|h2|h3|p|li|blockquote)\b[^>]*>(.*?)</\1>", re.DOTALL | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.I)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.DOTALL | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")

MIN_BLOCK_CHARS = 20
# What a path looks like, as opposed to a sentence you pasted. Anything matching this
# has to exist on disk: silently treating a typo'd path as prose makes a garbage draft.
_LOOKS_LIKE_PATH = re.compile(r"^[~./]|^[\w.\-/]+\.(?:md|markdown|txt|rst|html?|json|tex)$")
_LOOKS_LIKE_ARXIV = re.compile(r"^(?:arxiv[:/]|\d{4}\.\d+$)", re.IGNORECASE)


class IngestError(ValueError):
    """Something about the source, not about our code. The CLI prints it as one line."""


@dataclass(slots=True)
class Material:
    """Your own source material, normalised."""

    title: str
    text: str
    url: str = ""
    kind: str = "text"  # text | article | paper
    authors: list[str] = field(default_factory=list)

    def truncated(self) -> str:
        return self.text[: settings.ingest_max_chars]


def _strip(html: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


def from_text(text: str, *, title: str = "", url: str = "") -> Material:
    body = text.strip()
    if not title:
        first = body.splitlines()[0] if body else ""
        title = first.lstrip("# ").strip()[:120]
    return Material(title=title or "untitled note", text=body, url=url, kind="text")


def from_file(path: Path) -> Material:
    """A leading markdown heading is the title when there is one; the filename otherwise."""
    material = from_text(path.read_text())
    if not material.title or material.title == "untitled note":
        material.title = path.stem.replace("-", " ").replace("_", " ")
    return material


def from_html(html: str, url: str) -> Material:
    body = _SCRIPT.sub(" ", html)
    blocks = [_strip(match.group(2)) for match in _BLOCK.finditer(body)]
    text = "\n\n".join(block for block in blocks if len(block) >= MIN_BLOCK_CHARS)
    heading = _H1.search(body)
    title = _TITLE.search(body)
    return Material(
        title=(_strip(heading.group(1)) if heading else "")
        or (_strip(title.group(1)) if title else "")
        or url,
        text=text,
        url=url,
        kind="article",
    )


def from_arxiv(arxiv_id: str, client: httpx.Client) -> Material:
    """The abstract page is mostly chrome; the Atom API gives the abstract and authors."""
    try:
        response = client.get(
            ARXIV_API, params={"id_list": arxiv_id, "max_results": 1}, timeout=TIMEOUT
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise IngestError(f"could not reach arXiv for {arxiv_id}: {exc}") from exc
    entries = feedparser.parse(response.text).entries
    if not entries or not str(entries[0].get("summary", "")).strip():
        raise IngestError(f"arXiv has no paper {arxiv_id}")
    entry = entries[0]
    return Material(
        title=_WS.sub(" ", entry.get("title", arxiv_id)).replace("\n", " ").strip(),
        text=_WS.sub(" ", entry.get("summary", "")).replace("\n", " ").strip(),
        url=f"https://arxiv.org/abs/{arxiv_id}",
        kind="paper",
        authors=[author.get("name", "") for author in entry.get("authors", [])],
    )


def from_url(url: str, client: httpx.Client | None = None) -> Material:
    owns = client is None
    client = client or httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        arxiv = ARXIV_URL.search(url)
        if arxiv:
            return from_arxiv(arxiv.group(1), client)
        try:
            response = client.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise IngestError(f"{url} returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise IngestError(f"could not fetch {url}: {exc}") from exc
        kind = response.headers.get("content-type", "").split(";")[0].strip()
        if kind and not kind.startswith(("text/", "application/xhtml")):
            raise IngestError(f"{url} is {kind}, not a page I can read")
        material = from_html(response.text, url)
        if not material.text.strip():
            raise IngestError(f"no readable article text at {url}")
        return material
    finally:
        if owns:
            client.close()


def load(source: str, client: httpx.Client | None = None) -> Material:
    """One entry point for the CLI: a URL, an arXiv id, a file path, or literal text."""
    candidate = source.strip()
    if candidate.startswith(("http://", "https://")):
        return from_url(candidate, client)
    arxiv = ARXIV_ID.match(candidate)
    if arxiv:
        return from_url(f"https://arxiv.org/abs/{arxiv.group(1)}", client)
    if " " not in candidate and _LOOKS_LIKE_ARXIV.match(candidate):
        raise IngestError(f"{candidate!r} is not a valid arXiv id (expected 2401.12345)")
    path = Path(candidate).expanduser()
    if len(candidate) < 400 and "\n" not in candidate and _LOOKS_LIKE_PATH.match(candidate):
        if not path.is_file():
            raise IngestError(f"no such file: {path}")
        return from_file(path)
    if path.is_file():
        return from_file(path)
    if not candidate:
        raise IngestError("nothing readable in that source")
    return from_text(candidate)
