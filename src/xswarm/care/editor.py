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
# Promises no publisher can make: an outcome asserted for the reader.
PROMISE_RE = re.compile(
    r"\b(?:will|get|gets)\s+(?:be\s+)?(?:reimbursed|paid|covered|approved)\b"
    r"|\bwill\s+(?:pay|reimburse|cover|approve)\b"
    r"|\bwe\s+guarantee\b|\balways\s+(?:covered|reimbursed|approved)\b",
    re.IGNORECASE,
)
# Describing how payment works. Only a promise once it claims to hold every time.
PAYMENT_RE = re.compile(
    r"\b(?:reimburses|pays|covers|approves)\b|\b(?:are|is)\s+(?:reimbursed|paid|covered)\b",
    re.IGNORECASE,
)
# Assurance beats hedging: "you can be sure" is a promise however softly it is phrased.
ASSURANCE_RE = re.compile(
    r"\b(?:can be sure|rest assured|no need to worry|guaranteed|without exception)\b",
    re.IGNORECASE,
)
# A conditional does not rescue a claim that also says it holds every time.
UNIVERSAL_RE = re.compile(
    r"\b(?:every|all|each|any|always|in full|automatically)\b", re.IGNORECASE
)
# Personalised clinical direction. Teaching a skill is fine; directing care is not.
CLINICAL_DIRECTIVE_RE = re.compile(
    r"\byou should (?:stop|start|take|give|administer|increase|decrease|reduce|adjust"
    r"|discontinue|switch|withhold|double)\b"
    # Any imperative aimed at a medication, however it is framed.
    r"|\b(?:stop|start|adjust|increase|reduce|double|halve|give|administer|withhold|skip)"
    r"\s+(?:the|your|their|his|her|an?|another|extra)?\s*"
    r"(?:medication|medicine|dose|dosage|insulin|oxygen|pills?)\b"
    r"|\bdo not (?:call|contact) (?:your|their) (?:doctor|clinician|physician)\b",
    re.IGNORECASE,
)
# Sentences that read as first-person patient anecdote invite invented PHI.
INVENTED_PATIENT_RE = re.compile(
    r"\b(?:one of our patients|a patient of ours|my patient|our client,)\b"
    r"|\ba (?:caregiver|patient|family|client|daughter|son|spouse|wife|husband)"
    r"\s+(?:we|I)\s+(?:worked with|work with|met|spoke with|saw|helped|trained)\b"
    r"|\b(?:a|one) (?:family|caregiver|patient|daughter|son)\b[^.]{0,60}?"
    r"\btold (?:us|me)\b",
    re.IGNORECASE,
)
# "Maria, 68," — a named person with an age is either real PHI or invented.
NAMED_AGE_RE = re.compile(r"\b[A-Z][a-z]+,\s?\d{1,3},")
# Conditionals turn a promise back into an accurate description of how coverage works.
HEDGE_RE = re.compile(
    r"\b(?:may|might|can|could|if|when|where|subject to|typically|generally|often|depends)\b",
    re.IGNORECASE,
)
# Alverna sells into the United States: Medicare, CMS rules, US hospitals and clinicians.
# A post that opens on a Taiwanese cohort reads like a literature review, and it is
# evidence about a health system the reader does not work in.
NON_US_MARKERS = (
    "taiwan",
    "taiwanese",
    "china",
    "chinese",
    "japan",
    "japanese",
    "korea",
    "korean",
    "singapore",
    "india",
    "iran",
    "israel",
    "turkey",
    "brazil",
    "mexico",
    "canada",
    "canadian",
    "australia",
    "australian",
    "new zealand",
    "united kingdom",
    "britain",
    "british",
    "nhs",
    "ireland",
    "europe",
    "european",
    "germany",
    "german",
    "france",
    "french",
    "spain",
    "spanish",
    "italy",
    "italian",
    "netherlands",
    "dutch",
    "sweden",
    "swedish",
    "norway",
    "denmark",
    "danish",
    "finland",
)
# Word boundaries matter: "Indiana" is a US state and "India" is not the same word.
NON_US_RE = re.compile(rf"\b(?:{'|'.join(NON_US_MARKERS)})\b", re.IGNORECASE)
PAYER_RE = re.compile(
    r"\b(?:medicare|medicaid|payer|payor|insurer|insurance|plan)\b", re.IGNORECASE
)
# Any figure, used to decide whether a regulatory sentence is asserting something exact.
NUMBER_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?\s?(?:%|percent|million|billion|days?|hours?|minutes?)\b",
    re.IGNORECASE,
)
# A figure that reads as evidence and therefore needs a source. Plain durations
# ("a 30-minute session") describe the product and are deliberately excluded.
STAT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:%|percent)\b"
    r"|\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?\s?(?:million|billion)\b"
    r"|\b\d+\s+in\s+\d+\b"
    r"|\b[\w-]+\s+(?:out of|of every)\s+\d+\b",
    re.IGNORECASE,
)


def _is_authoritative(host: str) -> bool:
    return any(host.endswith(allowed) for allowed in settings.care_authoritative_hosts)


def _is_signal(host: str) -> bool:
    return any(host.endswith(signal) for signal in settings.care_signal_hosts)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BREAK.split(text) if part.strip()]


def promises_outcome(sentence: str) -> bool:
    """A payment or coverage guarantee. Questions and hedged statements are not one."""
    if sentence.rstrip().endswith("?") or not PAYER_RE.search(sentence):
        return False
    promised = bool(PROMISE_RE.search(sentence))
    if not promised and not PAYMENT_RE.search(sentence):
        return False
    if ASSURANCE_RE.search(sentence) or UNIVERSAL_RE.search(sentence):
        return True  # a guarantee is a guarantee, conditionals notwithstanding
    return promised and not HEDGE_RE.search(sentence)


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
        if INVENTED_PATIENT_RE.search(sentence) or NAMED_AGE_RE.search(sentence):
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
        elif STAT_RE.search(sentence) and not _cited(sentence):
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
