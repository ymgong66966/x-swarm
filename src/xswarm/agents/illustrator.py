"""Illustrator: decides what a generated image should depict, in the house style.

Separate from the Visualizer because the two answer different questions. The Visualizer
plots numbers that exist; the Illustrator draws the idea when there are no numbers worth
plotting. Keeping them apart is what stops a model from inventing a chart.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import settings
from ..imagegen import ArtSpec, art_direction, generate
from ..llm import LLM, load_prompt
from ..models import STREAM_ML, Article, Asset, Brief, Draft

log = logging.getLogger(__name__)

# Site banners are photographic; everything on the timeline stays illustrated.
HERO_STYLE = "site_photo"

# Which look fits which kind of argument, when no model is available to choose.
_FALLBACK_STYLES = {
    "explainer": "frontier_diagram",
    "paper_of_the_day": "frontier_diagram",
    "counterpoint": "risk_dark",
    "roundup": "data_poster",
    "own_work": "concept_hero",
    # Care pillars. Health content never gets the aggressive look.
    "policy_explainer": "clinical_calm",
    "reimbursement_mechanics": "clinical_calm",
    "caregiver_skills": "clinical_calm",
    "transitions_of_care": "clinical_calm",
    "clinician_career": "clinical_calm",
    "field_signal": "clinical_calm",
}


def _fallback_spec(draft: Draft, brief: Brief | None) -> ArtSpec:
    """No model: draw the topic, not the argument. Deliberately plain, because a
    fallback that guesses at meaning is how an image ends up contradicting the post."""
    pillar = str((draft.features or {}).get("pillar", ""))
    subject = (brief.whats_new if brief else "") or draft.body
    style = _FALLBACK_STYLES.get(pillar, settings.default_art_style)
    return ArtSpec(
        style=style,
        subject=f"An abstract systems illustration representing: {subject[:220]}",
        emphasis="the central mechanism",
        alt_text=f"Abstract illustration representing {subject[:180]}",
    )


def forced_style(draft: Draft) -> str:
    """`xswarm illustrate --style` pins the look; the model still writes the subject."""
    style = str((draft.features or {}).get("art_style", ""))
    return style if style in settings.art_styles else ""


def agent_name(draft: Draft) -> str:
    """Cost lands on the stream that asked for the picture, not on a shared bucket."""
    return f"illustrator_{draft.stream or STREAM_ML}"


def build_spec(draft: Draft, brief: Brief | None, llm: LLM) -> ArtSpec:
    payload = llm.complete_json(
        load_prompt("illustrator").format(
            body="\n\n".join([draft.body, *(draft.thread or [])]),
            whats_new=brief.whats_new if brief else "",
            what_it_replaces=brief.what_it_replaces if brief else "",
            key_number=brief.key_number if brief else "",
            caveat=brief.caveat if brief else "",
            grounded_claims="\n".join(f"- {c}" for c in (brief.grounded_claims or []))
            if brief
            else str((draft.features or {}).get("grounding", ""))[:2000],
            art_direction=art_direction(),
            styles=", ".join(settings.art_styles),
        ),
        strong=False,
        max_tokens=700,
        agent=agent_name(draft),
    )
    forced = forced_style(draft)
    if not isinstance(payload, dict):
        spec = _fallback_spec(draft, brief)
    else:
        try:
            spec = ArtSpec.model_validate(payload)
        except ValidationError as exc:
            log.warning("art spec rejected for draft %s: %s", draft.id, exc.error_count())
            spec = _fallback_spec(draft, brief)
        if not spec.subject.strip():
            spec = _fallback_spec(draft, brief)
    if spec.style not in settings.art_styles:
        spec.style = _fallback_spec(draft, brief).style
    if forced:
        spec.style = forced
    if not spec.alt_text.strip():
        spec.alt_text = f"Abstract illustration: {spec.subject[:180]}"
    return spec


def article_spec(article: Article, llm: LLM) -> ArtSpec:
    """The banner for a site article: a photograph of the caregiving situation the piece
    is about. A different art director from the timeline's, because the timeline one is
    told never to draw a face — which is how these banners ended up as still lifes of
    furniture, recognisable to nobody."""
    payload = llm.complete_json(
        load_prompt("illustrator_photo").format(
            body=f"{article.title}\n\n{article.dek}\n\n{article.body_md[:4000]}",
            whats_new=article.thesis or article.meta_description,
            grounded_claims="\n".join(f"- {claim}" for claim in (article.evidence or [])[:12]),
            art_direction=art_direction(),
        ),
        strong=False,
        max_tokens=700,
        agent="illustrator_care",
    )
    spec = ArtSpec(style=HERO_STYLE)
    if isinstance(payload, dict):
        try:
            spec = ArtSpec.model_validate({**payload, "style": HERO_STYLE})
        except ValidationError as exc:
            log.warning("art spec rejected for article %s: %s", article.id, exc.error_count())
    if not spec.subject.strip():
        spec.subject = (
            "A family caregiver and a visiting nurse working together with an older adult "
            f"at home, in the situation described by: {article.title}"
        )
    if not spec.alt_text.strip():
        spec.alt_text = f"Photograph: {spec.subject[:180]}"
    return spec


def illustrate_article(article: Article, llm: LLM) -> tuple[Path, str] | None:
    """Shoot an article's hero. Returns the file and its alt text, or None when the image
    provider is unavailable — an article without a banner is still publishable.

    JPEG rather than PNG: this is a photograph, and the site's palette squeeze for flat
    art would put banding through skin tone and window light.
    """
    spec = article_spec(article, llm)
    path = settings.assets_dir / f"article-{article.id}-hero.jpg"
    drawn = generate(
        spec,
        path,
        llm,
        agent="illustrator_care",
        model=settings.hero_image_model,
        quality=settings.hero_image_quality,
    )
    if drawn is None:
        return None
    return path, spec.alt_text


def illustrate(session: Session, draft: Draft, llm: LLM) -> Asset | None:
    """Generate one image for a draft. Returns None when no image could be made, so
    the caller can fall back to a rendered template rather than ship a bare post."""
    spec = build_spec(draft, draft.brief, llm)
    path = settings.assets_dir / f"draft-{draft.id}-{spec.style}.png"
    if generate(spec, path, llm, agent=agent_name(draft)) is None:
        return None
    asset = Asset(
        draft_id=draft.id,
        kind="generated_art",
        path=str(path),
        alt_text=spec.alt_text[:1000],
        spec=spec.model_dump(),
    )
    session.add(asset)
    draft.alt_text = asset.alt_text
    return asset
