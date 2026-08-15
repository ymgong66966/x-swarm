"""Text-to-image, in one house style.

The look lives in `prompts/art_direction.md` rather than in code, so the account's
visual identity is editable the same way `voice.md` is. A model only ever supplies the
*subject* of the image; the style block and the hard constraints are appended here, so
no draft can quietly change what the account looks like — or ask for text in an image,
which is where generated visuals usually embarrass a technical account.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .config import settings
from .llm import LLM, load_prompt

log = logging.getLogger(__name__)

# Everything the model is not allowed to put in the picture, restated at the end of the
# prompt where image models weight it most heavily.
CONSTRAINTS = (
    "No text, no words, no letters, no numbers, no labels, no axis ticks, no logos, "
    "no watermarks, no human faces, no stock-photo people. Flat editorial illustration, "
    "not a photograph, not 3D chrome."
)
_SECTION = re.compile(r"^### (\w+)\s*$", re.MULTILINE)
# Styles that define their own ground because they ship somewhere other than the
# timeline: the always-applied near-black would read as a hole in the site's cream page.
LIGHT_STYLES = {"site_hero"}


class ArtSpec(BaseModel):
    """What the Illustrator decides. Small on purpose: style is chosen from a fixed
    list, and the free-text part is only ever the subject of the picture."""

    style: str = Field(default_factory=lambda: settings.default_art_style)
    subject: str = ""
    emphasis: str = ""
    alt_text: str = ""


def art_direction() -> str:
    return load_prompt("art_direction")


def house_style() -> str:
    """The always-applied part: everything above the named-style sections."""
    text = art_direction()
    head, _, _ = text.partition("## Named styles")
    _, _, body = head.partition("## House style (always applied)")
    return body.strip()


def style_block(style: str) -> str:
    """The paragraph describing one named style, or an empty string if unknown."""
    text = art_direction()
    sections = list(_SECTION.finditer(text))
    for index, match in enumerate(sections):
        if match.group(1) != style:
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        return text[match.end() : end].strip()
    return ""


def build_prompt(spec: ArtSpec) -> str:
    parts = [spec.subject.strip() or spec.emphasis.strip()]
    if spec.emphasis.strip() and spec.emphasis.strip() not in parts[0]:
        parts.append(f"The composition leads with {spec.emphasis.strip()}.")
    block = style_block(spec.style)
    if block:
        parts.append(block)
    if spec.style not in LIGHT_STYLES:
        parts.append(house_style())
    parts.append(CONSTRAINTS)
    return "\n\n".join(part for part in parts if part)


TEXT_QUESTION = (
    "Does this image contain any legible written words, letters, numbers, labels or "
    "watermarks? Answer with one word: YES or NO."
)
RETRY_SUFFIX = (
    "\n\nCritical: the previous attempt contained written words. Draw shapes only. "
    "Any glyph, label, caption or number is a failed image."
)


def has_text(data: bytes, llm: LLM, *, agent: str = "illustrator") -> bool:
    """Whether the model can read words in its own output. A no-answer counts as clean,
    so an unavailable checker never blocks an image."""
    verdict = llm.look(data, TEXT_QUESTION, agent=agent)
    return verdict is not None and verdict.strip().upper().startswith("YES")


def generate(spec: ArtSpec, path: Path, llm: LLM, *, agent: str = "illustrator") -> Path | None:
    """Render one image to disk. Returns None when no image provider is available or
    when every attempt came back with text in it, so the caller falls back to a card.

    Image models honour "no text" maybe two times in three, and typography they invent
    is always subtly wrong, so a text-bearing image is treated as a failed generation.
    """
    prompt = build_prompt(spec)
    for attempt in range(2):
        data = llm.image(prompt if attempt == 0 else prompt + RETRY_SUFFIX, agent=agent)
        if data is None:
            return None
        if has_text(data, llm, agent=agent):
            log.warning("generated image %s has text in it (attempt %s)", spec.style, attempt + 1)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log.info("generated image %s in style %s", path.name, spec.style)
        return path
    return None
