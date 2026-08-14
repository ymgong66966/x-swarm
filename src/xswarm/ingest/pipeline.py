"""Your own material -> a reviewed, illustrated, schedulable X thread.

This stream skips Scout/Curator/Analyst entirely: you already decided the material is
worth posting, so the only jobs left are writing it in voice, illustrating it, and
running the same Editor gate the automated stream goes through. Everything the Editor
checks numbers against is the material itself, stored on the draft, so a figure that is
not in your source cannot reach a post.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from ..agents import editor, publisher
from ..agents.illustrator import illustrate
from ..config import settings
from ..llm import LLM, load_prompt
from ..models import STREAM_OWN, Asset, Draft, Publication
from ..publishers import TypefullyClient
from .fetch import IngestError, Material

log = logging.getLogger(__name__)

MIN_THREAD_POSTS = 2
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
PILLAR = "own_work"


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _fallback_posts(material: Material) -> list[str]:
    """No model: chunk the material's own sentences. Nothing is paraphrased, so nothing
    can be misstated — it reads flat, and it is always safe to review."""
    posts: list[str] = []
    current = ""
    for sentence in SENTENCE_RE.split(material.text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= settings.max_post_chars:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            posts.append(current)
        current = sentence[: settings.max_post_chars]
        if len(posts) >= settings.max_thread_posts:
            break
    if current and len(posts) < settings.max_thread_posts + 1:
        posts.append(current)
    return posts[: settings.max_thread_posts + 1]


def write_thread(material: Material, llm: LLM) -> tuple[list[str], str, list[str]]:
    """(posts, link reply, claims the posts rest on)."""
    payload = llm.complete_json(
        load_prompt("ingest_thread").format(
            voice=_read(settings.voice_path),
            title=material.title,
            kind=material.kind,
            url=material.url or "(no link)",
            text=material.truncated(),
            min_posts=MIN_THREAD_POSTS,
            max_posts=settings.max_thread_posts,
            max_chars=settings.max_post_chars,
        ),
        strong=True,
        max_tokens=2000,
        agent="ingest_writer",
    )
    if not isinstance(payload, dict):
        return _fallback_posts(material), _link_reply(material, ""), []
    posts = [str(p).strip() for p in payload.get("posts", []) if str(p).strip()]
    if not posts:
        posts = _fallback_posts(material)
    claims = [str(c).strip() for c in payload.get("claims", []) if str(c).strip()]
    return (
        posts[: settings.max_thread_posts + 1],
        _link_reply(material, str(payload.get("link_reply", ""))),
        claims,
    )


def _link_reply(material: Material, suggested: str) -> str:
    """The link always ships in the trailing reply, never in the posts themselves —
    X prices link posts separately and the Editor blocks URLs in the body."""
    if not material.url:
        return ""
    if material.url in suggested:
        return suggested.strip()
    return f"{suggested.strip()} {material.url}".strip()


# The formats X accepts, identified by their magic bytes rather than by extension.
_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def check_image(path: Path) -> None:
    """Fail before anything is written. A mistyped path or a text file that reaches the
    publisher as an attachment is a broken post, so it is rejected here."""
    if not path.is_file():
        raise IngestError(f"no such image: {path}")
    header = path.open("rb").read(12)
    # WebP is a RIFF container, so the format tag at byte 8 is what identifies it.
    webp = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if not (webp or header.startswith(_IMAGE_MAGIC)):
        raise IngestError(f"{path} is not a PNG, JPEG, GIF or WebP image")


def attach_images(session: Session, draft: Draft, paths: list[Path], alt: str) -> list[Asset]:
    """Images you supplied yourself. Copied into the assets directory so the publisher
    reads them from one place and the original cannot be moved out from under it."""
    assets: list[Asset] = []
    for index, source in enumerate(paths):
        check_image(source)
        destination = settings.assets_dir / f"draft-{draft.id}-own-{index}{source.suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        asset = Asset(
            draft_id=draft.id,
            kind="user_image",
            path=str(destination),
            alt_text=alt[:1000],
            spec={"original": str(source)},
        )
        session.add(asset)
        assets.append(asset)
    if assets:
        draft.alt_text = alt[:1000]
    return assets


def build(
    session: Session,
    material: Material,
    llm: LLM,
    *,
    images: list[Path] | None = None,
    alt: str = "",
    illustrate_it: bool = True,
) -> Draft:
    posts, link_reply, claims = write_thread(material, llm)
    draft = Draft(
        stream=STREAM_OWN,
        variant=0,
        body=posts[0],
        thread=posts[1:],
        link_reply=link_reply,
        features={
            "pillar": PILLAR,
            "source_url": material.url,
            "source_title": material.title,
            "source_kind": material.kind,
            "source_authors": material.authors,
            # What the Editor checks every number and claim against.
            "grounding": material.truncated(),
            "claims": claims,
        },
        status="drafted",
    )
    session.add(draft)
    session.flush()

    supplied = attach_images(session, draft, list(images or []), alt or material.title)
    if not supplied and illustrate_it:
        asset = illustrate(session, draft, llm)
        if asset is not None:
            draft.features = {**draft.features, "visual_hint": asset.kind}
    elif supplied:
        draft.features = {**draft.features, "visual_hint": "user_image"}
    session.flush()
    return draft


def run(
    session: Session,
    material: Material,
    llm: LLM,
    *,
    images: list[Path] | None = None,
    alt: str = "",
    illustrate_it: bool = True,
) -> Draft:
    """Build the thread and run it through the Editor. Never schedules: publishing is
    a separate, human-triggered step."""
    draft = build(session, material, llm, images=images, alt=alt, illustrate_it=illustrate_it)
    editor.run(session, llm, [draft])
    log.info("ingested %r -> draft %s (%s)", material.title[:60], draft.id, draft.status)
    return draft


def schedule(
    session: Session,
    draft: Draft,
    when: dt.datetime | None = None,
    *,
    dry_run: bool = False,
    plan_only: bool = True,
) -> Publication:
    """Hand an approved ingest draft to Typefully. Approval is the human gate: a draft
    that has not been approved is never sent, whatever the caller asks for."""
    if draft.status != "approved":
        raise ValueError(f"draft {draft.id} is {draft.status}, not approved")
    slot = when or publisher.next_slots(1, taken=publisher.queued_times(session))[0]
    client = None if dry_run or not settings.typefully_api_key else TypefullyClient()
    return publisher.publish(session, draft, slot, client=client, plan_only=plan_only)
