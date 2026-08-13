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
# First-person experience the account has not actually had. The voice is opinionated,
# which makes it easy for the Writer to slide from "the paper reports" into "I ran it".
FIRSTHAND_RE = re.compile(
    r"\b(?:i|we)\s+(?:ran|tested|reproduced|benchmarked|tried|deployed|measured)\b"
    r"|\bin (?:my|our) (?:tests?|experiments?|benchmarks?)\b",
    re.IGNORECASE,
)
DUPLICATE_THRESHOLD = 85


def _grounding_text(draft: Draft, brief: Brief | None) -> str:
    """Everything the draft is allowed to assert. Roundup threads carry their own
    grounding because they have no single brief behind them."""
    if brief is None:
        return str((draft.features or {}).get("grounding", ""))
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


def deterministic_checks(
    draft: Draft, brief: Brief | None, recent_bodies: list[str]
) -> list[str]:
    """Everything that can be decided without a model. Cheap, and it catches the
    failure modes that actually kill a technical account."""
    body = draft.body
    posts = [body, *(draft.thread or [])]
    notes: list[str] = []
    grounding = _grounding_text(draft, brief)

    if not body.strip():
        notes.append("empty body")
    if brief is not None and not brief.grounded_claims:
        notes.append("brief has no grounded claims — nothing in this post is verifiable")
    if brief is None and not grounding.strip():
        notes.append("no grounding attached — nothing in this thread is verifiable")

    for index, post in enumerate(posts):
        label = "post" if index == 0 else f"thread post {index + 1}"
        if index and not post.strip():
            notes.append(f"{label} is empty")
        if len(post) > settings.max_post_chars:
            notes.append(f"{label} too long: {len(post)} > {settings.max_post_chars} chars")
        if URL_RE.search(post):
            notes.append(f"URL in {label} — links belong in the trailing reply")
        if HASHTAG_RE.search(post):
            notes.append("hashtags are off-brand")

        lowered_post = post.lower()
        for phrase in settings.banned_phrases:
            if phrase.lower() in lowered_post:
                notes.append(f"banned phrase in {label}: {phrase!r}")
        if FIRSTHAND_RE.search(post):
            notes.append(f"{label} claims first-hand experience the brief cannot support")
        if post.count("—") > 1:
            notes.append(f"em-dash cadence in {label} reads as LLM output")
        for number in set(NUMBER_RE.findall(post)):
            if number not in grounding:
                notes.append(f"number {number!r} in {label} is not present in the brief")

    lowered = body.lower()
    for claim in (brief.unverified_claims if brief else []) or []:
        if claim and fuzz.partial_ratio(claim.lower(), lowered) > 90:
            notes.append(f"repeats an unverified claim: {claim[:60]!r}")

    for past in recent_bodies:
        if fuzz.token_set_ratio(body, past) > DUPLICATE_THRESHOLD:
            notes.append("near-duplicate of a recent post")
            break

    if (draft.features or {}).get("visual_hint") and not draft.alt_text:
        notes.append("missing alt text for the attached visual")

    return notes


def critic_check(draft: Draft, brief: Brief | None, llm: LLM) -> list[str]:
    if brief is None:
        return []
    payload = llm.complete_json(
        load_prompt("editor").format(
            body="\n\n---\n\n".join([draft.body, *(draft.thread or [])]),
            whats_new=brief.whats_new,
            key_number=brief.key_number,
            caveat=brief.caveat,
            grounded_claims="\n".join(f"- {c}" for c in brief.grounded_claims or []),
        ),
        strong=True,
        max_tokens=800,
        agent="editor",
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
