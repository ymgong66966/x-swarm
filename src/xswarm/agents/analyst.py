from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Brief, Candidate

log = logging.getLogger(__name__)

VISUAL_TEMPLATES = {
    "concept_diagram",
    "result_chart",
    "comparison_table",
    "annotated_figure",
    "quote_card",
}


def _fallback_brief(candidate: Candidate) -> Brief:
    """Without an LLM we still produce a brief, but every claim is marked unverified so
    the Editor blocks it. Silence beats a confident wrong summary."""
    item = candidate.item
    return Brief(
        candidate_id=candidate.id,
        whats_new=item.title,
        key_number="",
        caveat="Generated without model access; not reviewed.",
        unverified_claims=[item.title],
        visual_hint="concept_diagram",
    )


def analyze(candidate: Candidate, llm: LLM) -> Brief:
    item = candidate.item
    payload = llm.complete_json(
        load_prompt("analyst").format(
            title=item.title,
            authors=", ".join(item.authors[:8]),
            source=item.source,
            url=item.url,
            summary=item.summary[:6000],
            signals=item.signals,
        ),
        strong=True,
        max_tokens=2000,
    )
    if not isinstance(payload, dict):
        return _fallback_brief(candidate)

    visual_hint = str(payload.get("visual_hint", "concept_diagram"))
    return Brief(
        candidate_id=candidate.id,
        whats_new=str(payload.get("whats_new", "")).strip(),
        what_it_replaces=str(payload.get("what_it_replaces", "")).strip(),
        key_number=str(payload.get("key_number", "")).strip(),
        caveat=str(payload.get("caveat", "")).strip(),
        builder_takeaway=str(payload.get("builder_takeaway", "")).strip(),
        grounded_claims=[str(c) for c in payload.get("grounded_claims", [])],
        unverified_claims=[str(c) for c in payload.get("unverified_claims", [])],
        visual_hint=visual_hint if visual_hint in VISUAL_TEMPLATES else "concept_diagram",
    )


def run(session: Session, llm: LLM, candidates: list[Candidate]) -> list[Brief]:
    briefs: list[Brief] = []
    for candidate in candidates[: settings.briefs_per_day]:
        brief = analyze(candidate, llm)
        session.add(brief)
        briefs.append(brief)
    session.flush()
    log.info("wrote %d briefs", len(briefs))
    return briefs
