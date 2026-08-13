from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Brief, Draft

log = logging.getLogger(__name__)

HOOK_STYLES = ["claim", "number", "contrarian"]


def _read(path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _link_reply(brief: Brief) -> str:
    """Links suppress reach on X and cost 13x more to post through the API, so they
    always live in a trailing reply rather than the main post."""
    url = brief.candidate.item.url
    return f"Paper: {url}" if url else ""


def _fallback_body(brief: Brief, hook_style: str) -> str:
    lead = brief.key_number if hook_style == "number" and brief.key_number else brief.whats_new
    parts = [lead, brief.builder_takeaway, brief.caveat]
    body = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return body[: settings.max_post_chars]


def write(brief: Brief, llm: LLM, voice: str, playbook: str) -> list[Draft]:
    payload = llm.complete_json(
        load_prompt("writer").format(
            voice=voice,
            playbook=playbook,
            pillar=brief.candidate.pillar,
            max_chars=settings.max_post_chars,
            variants=settings.drafts_per_brief,
            whats_new=brief.whats_new,
            what_it_replaces=brief.what_it_replaces,
            key_number=brief.key_number,
            caveat=brief.caveat,
            builder_takeaway=brief.builder_takeaway,
            grounded_claims="\n".join(f"- {c}" for c in brief.grounded_claims),
        ),
        strong=True,
        max_tokens=2000,
    )

    drafts: list[Draft] = []
    link_reply = _link_reply(brief)
    if isinstance(payload, list) and payload:
        for index, variant in enumerate(payload[: settings.drafts_per_brief]):
            if not isinstance(variant, dict):
                continue
            drafts.append(
                Draft(
                    brief_id=brief.id,
                    variant=index,
                    body=str(variant.get("body", "")).strip(),
                    link_reply=link_reply,
                    alt_text=str(variant.get("alt_text", "")).strip(),
                    features={
                        "hook_style": variant.get("hook_style", HOOK_STYLES[index % 3]),
                        "pillar": brief.candidate.pillar,
                        "visual_hint": brief.visual_hint,
                    },
                )
            )
    if not drafts:
        for index, hook_style in enumerate(HOOK_STYLES[: settings.drafts_per_brief]):
            drafts.append(
                Draft(
                    brief_id=brief.id,
                    variant=index,
                    body=_fallback_body(brief, hook_style),
                    link_reply=link_reply,
                    features={
                        "hook_style": hook_style,
                        "pillar": brief.candidate.pillar,
                        "visual_hint": brief.visual_hint,
                        "fallback": True,
                    },
                )
            )
    return drafts


def run(session: Session, llm: LLM, briefs: list[Brief]) -> list[Draft]:
    voice = _read(settings.voice_path)
    playbook = _read(settings.playbook_path)
    drafts: list[Draft] = []
    for brief in briefs:
        for draft in write(brief, llm, voice, playbook):
            session.add(draft)
            drafts.append(draft)
    session.flush()
    log.info("wrote %d drafts", len(drafts))
    return drafts
