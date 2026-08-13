from __future__ import annotations

import datetime as dt
import logging
import re

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Brief, Draft

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"(?:^|\s)#\w+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
DUPLICATE_THRESHOLD = 85


def _grounding_text(brief: Brief) -> str:
    return " ".join(
        [
            brief.whats_new,
            brief.what_it_replaces,
            brief.key_number,
            brief.caveat,
            brief.builder_takeaway,
            " ".join(brief.grounded_claims or []),
            brief.candidate.item.title,
            brief.candidate.item.summary,
        ]
    )


def deterministic_checks(draft: Draft, brief: Brief, recent_bodies: list[str]) -> list[str]:
    """Everything that can be decided without a model. Cheap, and it catches the
    failure modes that actually kill a technical account."""
    body = draft.body
    notes: list[str] = []

    if not body.strip():
        notes.append("empty body")
    if not brief.grounded_claims:
        notes.append("brief has no grounded claims — nothing in this post is verifiable")
    if len(body) > settings.max_post_chars:
        notes.append(f"too long: {len(body)} > {settings.max_post_chars} chars")
    if URL_RE.search(body):
        notes.append("URL in the main post — links belong in the trailing reply")
    if HASHTAG_RE.search(body):
        notes.append("hashtags are off-brand")

    lowered = body.lower()
    for phrase in settings.banned_phrases:
        if phrase.lower() in lowered:
            notes.append(f"banned phrase: {phrase!r}")

    if body.count("—") > 1:
        notes.append("em-dash cadence reads as LLM output")

    grounding = _grounding_text(brief)
    for number in set(NUMBER_RE.findall(body)):
        if number not in grounding:
            notes.append(f"number {number!r} is not present in the brief")

    for claim in brief.unverified_claims or []:
        if claim and fuzz.partial_ratio(claim.lower(), lowered) > 90:
            notes.append(f"repeats an unverified claim: {claim[:60]!r}")

    for past in recent_bodies:
        if fuzz.token_set_ratio(body, past) > DUPLICATE_THRESHOLD:
            notes.append("near-duplicate of a recent post")
            break

    if (draft.features or {}).get("visual_hint") and not draft.alt_text:
        notes.append("missing alt text for the attached visual")

    return notes


def critic_check(draft: Draft, brief: Brief, llm: LLM) -> list[str]:
    payload = llm.complete_json(
        load_prompt("editor").format(
            body=draft.body,
            whats_new=brief.whats_new,
            key_number=brief.key_number,
            caveat=brief.caveat,
            grounded_claims="\n".join(f"- {c}" for c in brief.grounded_claims or []),
        ),
        strong=True,
        max_tokens=800,
    )
    if not isinstance(payload, dict):
        return []
    return [str(issue) for issue in payload.get("blocking_issues", [])]


def _recent_bodies(session: Session, exclude_ids: set[int]) -> list[str]:
    window = dt.timedelta(days=settings.repeat_topic_cooldown_days)
    cutoff = dt.datetime.now(dt.timezone.utc) - window
    rows = session.scalars(
        select(Draft.body).where(Draft.created_at >= cutoff, Draft.id.notin_(exclude_ids or {0}))
    )
    return list(rows)


def run(session: Session, llm: LLM, drafts: list[Draft]) -> list[Draft]:
    recent = _recent_bodies(session, {d.id for d in drafts if d.id})
    for draft in drafts:
        brief = draft.brief
        notes = deterministic_checks(draft, brief, recent)
        # Only pay for the critic on drafts that already passed the free checks.
        if not notes:
            notes = critic_check(draft, brief, llm)
        draft.editor_notes = notes
        draft.status = "blocked" if notes else "ready_for_review"
    session.flush()
    ready = sum(1 for d in drafts if d.status == "ready_for_review")
    log.info("editor passed %d/%d drafts", ready, len(drafts))
    return drafts
