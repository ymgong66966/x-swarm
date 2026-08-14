"""The care stream's safety gate.

Deterministic checks only. A model reviewing its own healthcare copy is not a control;
these rules are, and they run before anything reaches a human reviewer. Nothing here
tries to judge whether an article is good — only whether it is allowed to exist.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Article

log = logging.getLogger(__name__)

MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
# Split on terminators followed by whitespace, so a dot inside a URL stays inside it.
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")

# A statement that only a regulator can settle.
REGULATORY_RE = re.compile(
    r"\b(medicare|medicaid|cms|cpt|hcpcs|reimburs\w*|bill(?:able|ing|ed)?|covered|coverage"
    r"|copay|deductible|fee schedule|telehealth (?:rule|policy|waiver)s?)\b",
    re.IGNORECASE,
)
# Codes are the load-bearing detail of this subject; they may not be asserted loosely.
CODE_RE = re.compile(r"\b(?:9[67]\d{3}|G0\d{3})\b")
# Promises no publisher can make.
PROMISE_RE = re.compile(
    r"\b(?:will|are|is|get|gets)\s+(?:be\s+)?(?:reimbursed|paid|covered|approved)\b"
    r"|\bwe\s+guarantee\b|\balways\s+(?:covered|reimbursed|approved)\b",
    re.IGNORECASE,
)
# Personalised clinical direction. Teaching a skill is fine; directing care is not.
CLINICAL_DIRECTIVE_RE = re.compile(
    r"\byou should (?:stop|start|take|increase|decrease|reduce|adjust|discontinue|switch)\b"
    r"|\b(?:stop|start|adjust|increase|reduce) (?:your|their|his|her)"
    r" (?:medication|dose|dosage|insulin|oxygen)\b"
    r"|\bdo not (?:call|contact) (?:your|their) (?:doctor|clinician|physician)\b",
    re.IGNORECASE,
)
# Sentences that read as first-person patient anecdote invite invented PHI.
INVENTED_PATIENT_RE = re.compile(
    r"\b(?:one of our patients|a patient of ours|my patient|our client,)\b", re.IGNORECASE
)
# Conditionals turn a promise back into an accurate description of how coverage works.
HEDGE_RE = re.compile(
    r"\b(?:may|might|can|could|if|when|where|subject to|typically|generally|often|depends)\b",
    re.IGNORECASE,
)
PAYER_RE = re.compile(
    r"\b(?:medicare|medicaid|payer|payor|insurer|insurance|plan)\b", re.IGNORECASE
)
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent|million|billion|days?|hours?|minutes?)\b")


def _is_authoritative(host: str) -> bool:
    return any(host.endswith(allowed) for allowed in settings.care_authoritative_hosts)


def _is_signal(host: str) -> bool:
    return any(host.endswith(signal) for signal in settings.care_signal_hosts)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BREAK.split(text) if part.strip()]


def promises_outcome(sentence: str) -> bool:
    """A payment or coverage guarantee. Questions and hedged statements are not one."""
    if not PROMISE_RE.search(sentence) or sentence.rstrip().endswith("?"):
        return False
    if HEDGE_RE.search(sentence):
        return False
    return bool(PAYER_RE.search(sentence))


def _cited(sentence: str) -> bool:
    return bool(MARKDOWN_LINK.search(sentence))


def banned_phrase_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [
        phrase
        for phrase in settings.care_banned_phrases
        if re.search(rf"\b{re.escape(phrase)}\b", lowered)
    ]


def check(article: Article) -> list[str]:
    """Every reason this article may not be published, in plain language."""
    notes: list[str] = []
    body = article.body_md
    source_urls = {str(source.get("url", "")) for source in article.sources}

    if not article.thesis.strip():
        notes.append("no thesis: the piece does not commit to a point")
    if article.word_count < settings.care_min_words:
        notes.append(f"too short at {article.word_count} words")
    if "Drafted without model access" in body:
        notes.append("no model output: nothing was actually written")
    if settings.care_disclaimer not in body:
        notes.append("missing the education-not-advice disclaimer")
    if not article.meta_description.strip():
        notes.append("missing meta description")

    for phrase in banned_phrase_hits(body):
        notes.append(f"banned phrase: {phrase!r}")

    for sentence in _sentences(body):
        if promises_outcome(sentence):
            notes.append(f"promises an outcome: {sentence[:90]!r}")
        if CLINICAL_DIRECTIVE_RE.search(sentence):
            notes.append(f"personalised clinical direction: {sentence[:90]!r}")
        if INVENTED_PATIENT_RE.search(sentence):
            notes.append(f"invented patient anecdote: {sentence[:90]!r}")

        links = MARKDOWN_LINK.findall(sentence)
        # A code, or a rule stated with a figure attached, is a regulatory assertion.
        regulatory = bool(CODE_RE.search(sentence)) or bool(
            REGULATORY_RE.search(sentence) and NUMBER_RE.search(sentence)
        )
        if regulatory and not any(
            _is_authoritative(urllib.parse.urlsplit(url).netloc.lower()) for url in links
        ):
            notes.append(f"billing/code claim without a government citation: {sentence[:90]!r}")
        elif NUMBER_RE.search(sentence) and not _cited(sentence):
            notes.append(f"uncited statistic: {sentence[:90]!r}")

        if any(_is_signal(urllib.parse.urlsplit(url).netloc.lower()) for url in links) and (
            NUMBER_RE.search(sentence) or REGULATORY_RE.search(sentence)
        ):
            notes.append(f"forum/social link used as evidence: {sentence[:90]!r}")

    for url in MARKDOWN_LINK.findall(body):
        host = urllib.parse.urlsplit(url).netloc.lower()
        internal = host.endswith(urllib.parse.urlsplit(settings.care_site_url).netloc.lower())
        if url not in source_urls and not internal:
            notes.append(f"cites a URL that is not in the article's source list: {url}")

    if not any(
        _is_authoritative(urllib.parse.urlsplit(str(s.get("url", ""))).netloc.lower())
        or s.get("kind") in ("regulatory", "research")
        for s in article.sources
    ):
        notes.append("no authoritative source behind the piece")

    return notes


def review(session: Session, articles: list[Article]) -> list[Article]:
    passed: list[Article] = []
    for article in articles:
        notes = check(article)
        article.editor_notes = notes
        article.status = "blocked" if notes else "ready_for_review"
        if not notes:
            passed.append(article)
    session.flush()
    log.info("care editor passed %d of %d articles", len(passed), len(articles))
    return passed
