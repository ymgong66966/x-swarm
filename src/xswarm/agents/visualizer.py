from __future__ import annotations

import logging
import re

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Asset, Brief, Draft
from ..render import TEMPLATES, VisualSpec, alt_text, render

log = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")


def _fallback_spec(draft: Draft, brief: Brief) -> VisualSpec:
    """No model, or a spec we could not trust: fall back to typography, which can't
    misrepresent data the way an invented chart can."""
    if brief.key_number:
        number = NUMBER_RE.search(brief.key_number)
        if number:
            return VisualSpec(
                template="number_card",
                number=number.group(0),
                body=brief.key_number,
                caption=brief.caveat,
                source=brief.candidate.item.source,
            )
    return VisualSpec(
        template="quote_card",
        body=brief.whats_new or draft.body,
        caption=brief.builder_takeaway,
        source=brief.candidate.item.source,
    )


def build_spec(draft: Draft, brief: Brief, llm: LLM) -> VisualSpec:
    payload = llm.complete_json(
        load_prompt("visualizer").format(
            templates=", ".join(TEMPLATES),
            preferred=brief.visual_hint,
            body=draft.body,
            whats_new=brief.whats_new,
            what_it_replaces=brief.what_it_replaces,
            key_number=brief.key_number,
            caveat=brief.caveat,
            grounded_claims="\n".join(f"- {c}" for c in brief.grounded_claims or []),
            summary=brief.candidate.item.summary[:2000],
        ),
        strong=False,
        max_tokens=1200,
    )
    if not isinstance(payload, dict):
        return _fallback_spec(draft, brief)
    try:
        spec = VisualSpec.model_validate(payload)
    except ValidationError as exc:
        log.warning("visual spec rejected for draft %s: %s", draft.id, exc.error_count())
        return _fallback_spec(draft, brief)
    if spec.template == "result_chart" and len(spec.series) < 2:
        return _fallback_spec(draft, brief)
    if spec.template == "comparison_table" and not spec.rows:
        return _fallback_spec(draft, brief)
    if spec.template == "concept_diagram" and len(spec.stages) < 2:
        return _fallback_spec(draft, brief)
    if not spec.source:
        spec.source = brief.candidate.item.source
    return spec


def visualize(session: Session, draft: Draft, llm: LLM) -> Asset:
    brief = draft.brief
    spec = build_spec(draft, brief, llm)
    path = settings.assets_dir / f"draft-{draft.id}-{spec.template}.png"
    render(spec, path)
    asset = Asset(
        draft_id=draft.id,
        kind=spec.template,
        path=str(path),
        alt_text=alt_text(spec),
        spec=spec.model_dump(),
    )
    session.add(asset)
    # Alt text must describe what was actually drawn, not what the Writer imagined.
    draft.alt_text = asset.alt_text
    return asset


def _one_per_brief(drafts: list[Draft]) -> list[Draft]:
    """Only one variant per brief ever ships, so only one needs a rendered visual."""
    chosen: dict[int, Draft] = {}
    for draft in sorted(drafts, key=lambda d: d.variant):
        chosen.setdefault(draft.brief_id, draft)
    return list(chosen.values())


def run(session: Session, llm: LLM, drafts: list[Draft]) -> list[Asset]:
    assets = [visualize(session, draft, llm) for draft in _one_per_brief(drafts)]
    session.flush()
    log.info("rendered %d visuals", len(assets))
    return assets
