"""The care stream's safety gate.

Deterministic checks only. A model reviewing its own healthcare copy is not a control;
these rules are, and they run before anything reaches a human reviewer. Nothing here
tries to judge whether an article is good — only whether it is allowed to exist.

Rules are declared as data (RULES list) so they can be reviewed, tested one at a time,
and extended without touching the checking engine.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Article

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared patterns used by multiple rules
# ---------------------------------------------------------------------------

MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")

REGULATORY_RE = re.compile(
    r"\b(medicare|medicaid|cms|cpt|hcpcs|reimburs\w*|bill(?:able|ing|ed)?|covered|coverage"
    r"|copay|deductible|fee schedule|telehealth (?:rule|policy|waiver)s?)\b",
    re.IGNORECASE,
)
CODE_RE = re.compile(r"\b(?:9[67]\d{3}|G0\d{3})\b")
PROMISE_RE = re.compile(
    r"\b(?:will|get|gets)\s+(?:be\s+)?(?:reimbursed|paid|covered|approved)\b"
    r"|\bwill\s+(?:pay|reimburse|cover|approve)\b"
    r"|\bwe\s+guarantee\b|\balways\s+(?:covered|reimbursed|approved)\b",
    re.IGNORECASE,
)
PAYMENT_RE = re.compile(
    r"\b(?:reimburses|pays|covers|approves)\b|\b(?:are|is)\s+(?:reimbursed|paid|covered)\b",
    re.IGNORECASE,
)
ASSURANCE_RE = re.compile(
    r"\b(?:can be sure|rest assured|no need to worry|guaranteed|without exception)\b",
    re.IGNORECASE,
)
UNIVERSAL_RE = re.compile(
    r"\b(?:every|all|each|any|always|in full|automatically)\b", re.IGNORECASE
)
CLINICAL_DIRECTIVE_RE = re.compile(
    r"\byou should (?:stop|start|take|give|administer|increase|decrease|reduce|adjust"
    r"|discontinue|switch|withhold|double)\b"
    r"|\b(?:stop|start|adjust|increase|reduce|double|halve|give|administer|withhold|skip)"
    r"\s+(?:the|your|their|his|her|an?|another|extra)?\s*"
    r"(?:medication|medicine|dose|dosage|insulin|oxygen|pills?)\b"
    r"|\bdo not (?:call|contact) (?:your|their) (?:doctor|clinician|physician)\b",
    re.IGNORECASE,
)
INVENTED_PATIENT_RE = re.compile(
    r"\b(?:one of our patients|a patient of ours|my patient|our client,)\b"
    r"|\ba (?:caregiver|patient|family|client|daughter|son|spouse|wife|husband)"
    r"\s+(?:we|I)\s+(?:worked with|work with|met|spoke with|saw|helped|trained)\b"
    r"|\b(?:a|one) (?:family|caregiver|patient|daughter|son)\b[^.]{0,60}?"
    r"\btold (?:us|me)\b",
    re.IGNORECASE,
)
NAMED_AGE_RE = re.compile(r"\b[A-Z][a-z]+,\s?\d{1,3},")
HEDGE_RE = re.compile(
    r"\b(?:may|might|can|could|if|when|where|subject to|typically|generally|often|depends)\b",
    re.IGNORECASE,
)
PAYER_RE = re.compile(
    r"\b(?:medicare|medicaid|payer|payor|insurer|insurance|plan)\b", re.IGNORECASE
)
NUMBER_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?\s?(?:%|percent|million|billion|days?|hours?|minutes?)\b",
    re.IGNORECASE,
)
STAT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:%|percent)\b"
    r"|\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?\s?(?:million|billion)\b"
    r"|\b\d+\s+in\s+\d+\b"
    r"|\b[\w-]+\s+(?:out of|of every)\s+\d+\b",
    re.IGNORECASE,
)

NON_US_MARKERS = (
    "taiwan", "taiwanese", "china", "chinese", "japan", "japanese", "korea",
    "korean", "singapore", "india", "iran", "israel", "turkey", "brazil",
    "mexico", "canada", "canadian", "australia", "australian", "new zealand",
    "united kingdom", "britain", "british", "nhs", "ireland", "europe",
    "european", "germany", "german", "france", "french", "spain", "spanish",
    "italy", "italian", "netherlands", "dutch", "sweden", "swedish", "norway",
    "denmark", "danish", "finland",
)
NON_US_RE = re.compile(rf"\b(?:{'|'.join(NON_US_MARKERS)})\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------

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
        return True
    return promised and not HEDGE_RE.search(sentence)


def _cited(sentence: str) -> bool:
    return bool(MARKDOWN_LINK.search(sentence))


# ---------------------------------------------------------------------------
# Rule framework
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """One editorial compliance rule.

    `check` receives the article and returns a list of human-readable issues (empty
    means the rule passed). Keeping each rule in its own function makes it testable
    in isolation and keeps the engine loop trivial.
    """
    id: str
    description: str
    check: Callable[[Article], list[str]]
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Individual rule implementations
# ---------------------------------------------------------------------------

def _check_thesis(article: Article) -> list[str]:
    if not article.thesis.strip():
        return ["no thesis: the piece does not commit to a point"]
    return []


def _check_word_count(article: Article) -> list[str]:
    if article.word_count < settings.care_min_words:
        return [f"too short at {article.word_count} words"]
    return []


def _check_model_output(article: Article) -> list[str]:
    if "Drafted without model access" in article.body_md:
        return ["no model output: nothing was actually written"]
    return []


def _check_disclaimer(article: Article) -> list[str]:
    if settings.care_disclaimer not in article.body_md:
        return ["missing the education-not-advice disclaimer"]
    return []


def _check_meta_description(article: Article) -> list[str]:
    if not article.meta_description.strip():
        return ["missing meta description"]
    return []


def banned_phrase_hits(text: str) -> list[str]:
    """Phrases from the banned list that appear in the text. Also used by the promoter."""
    lowered = text.lower()
    return [
        phrase
        for phrase in settings.care_banned_phrases
        if re.search(rf"\b{re.escape(phrase)}\b", lowered)
    ]


def _check_banned_phrases(article: Article) -> list[str]:
    return [f"banned phrase: {phrase!r}" for phrase in banned_phrase_hits(article.body_md)]


def _check_promises(article: Article) -> list[str]:
    notes: list[str] = []
    for sentence in _sentences(article.body_md):
        if promises_outcome(sentence):
            notes.append(f"promises an outcome: {sentence[:90]!r}")
    return notes


def _check_clinical_directives(article: Article) -> list[str]:
    notes: list[str] = []
    for sentence in _sentences(article.body_md):
        if CLINICAL_DIRECTIVE_RE.search(sentence):
            notes.append(f"personalised clinical direction: {sentence[:90]!r}")
    return notes


def _check_invented_patients(article: Article) -> list[str]:
    notes: list[str] = []
    for sentence in _sentences(article.body_md):
        if INVENTED_PATIENT_RE.search(sentence) or NAMED_AGE_RE.search(sentence):
            notes.append(f"invented patient anecdote: {sentence[:90]!r}")
    return notes


def _check_regulatory_citations(article: Article) -> list[str]:
    notes: list[str] = []
    for sentence in _sentences(article.body_md):
        links = MARKDOWN_LINK.findall(sentence)
        regulatory = bool(CODE_RE.search(sentence)) or bool(
            REGULATORY_RE.search(sentence) and NUMBER_RE.search(sentence)
        )
        if regulatory and not any(
            _is_authoritative(urllib.parse.urlsplit(url).netloc.lower()) for url in links
        ):
            notes.append(f"billing/code claim without a government citation: {sentence[:90]!r}")
    return notes


def _check_uncited_stats(article: Article) -> list[str]:
    notes: list[str] = []
    for sentence in _sentences(article.body_md):
        # Skip sentences already caught by the regulatory rule.
        regulatory = bool(CODE_RE.search(sentence)) or bool(
            REGULATORY_RE.search(sentence) and NUMBER_RE.search(sentence)
        )
        if not regulatory and STAT_RE.search(sentence) and not _cited(sentence):
            notes.append(f"uncited statistic: {sentence[:90]!r}")
    return notes


def _check_signal_as_evidence(article: Article) -> list[str]:
    notes: list[str] = []
    for sentence in _sentences(article.body_md):
        links = MARKDOWN_LINK.findall(sentence)
        if any(_is_signal(urllib.parse.urlsplit(url).netloc.lower()) for url in links) and (
            NUMBER_RE.search(sentence) or REGULATORY_RE.search(sentence)
        ):
            notes.append(f"forum/social link used as evidence: {sentence[:90]!r}")
    return notes


def _check_source_list(article: Article) -> list[str]:
    source_urls = {str(source.get("url", "")) for source in article.sources}
    site_host = urllib.parse.urlsplit(settings.care_site_url).netloc.lower()
    notes: list[str] = []
    for url in MARKDOWN_LINK.findall(article.body_md):
        host = urllib.parse.urlsplit(url).netloc.lower()
        internal = host.endswith(site_host)
        if url not in source_urls and not internal:
            notes.append(f"cites a URL that is not in the article's source list: {url}")
    return notes


def _check_authoritative_backing(article: Article) -> list[str]:
    if not any(
        _is_authoritative(urllib.parse.urlsplit(str(s.get("url", ""))).netloc.lower())
        or s.get("kind") in ("regulatory", "research")
        for s in article.sources
    ):
        return ["no authoritative source behind the piece"]
    return []


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

RULES: list[Rule] = [
    Rule("thesis", "Article must commit to a thesis",
         _check_thesis, ["structure"]),
    Rule("word_count", "Article must meet minimum word count",
         _check_word_count, ["structure"]),
    Rule("model_output", "Article must contain real model output",
         _check_model_output, ["structure"]),
    Rule("disclaimer", "Education-not-advice disclaimer required",
         _check_disclaimer, ["compliance"]),
    Rule("meta_description", "Meta description required for SEO",
         _check_meta_description, ["seo"]),
    Rule("banned_phrases", "No banned marketing phrases",
         _check_banned_phrases, ["compliance"]),
    Rule("promises", "No outcome promises",
         _check_promises, ["compliance"]),
    Rule("clinical_directives", "No personalised clinical direction",
         _check_clinical_directives, ["compliance"]),
    Rule("invented_patients", "No invented patient anecdotes",
         _check_invented_patients, ["compliance"]),
    Rule("regulatory_citations", "Regulatory claims need government citations",
         _check_regulatory_citations, ["citations"]),
    Rule("uncited_stats", "Statistics must be cited",
         _check_uncited_stats, ["citations"]),
    Rule("signal_as_evidence", "Forum/social links cannot be used as evidence",
         _check_signal_as_evidence, ["citations"]),
    Rule("source_list", "Body links must appear in the source list",
         _check_source_list, ["citations"]),
    Rule("authoritative_backing", "At least one authoritative source required",
         _check_authoritative_backing, ["citations"]),
]


# ---------------------------------------------------------------------------
# Public API (unchanged signatures)
# ---------------------------------------------------------------------------

def check(article: Article) -> list[str]:
    """Every reason this article may not be published, in plain language."""
    notes: list[str] = []
    for rule in RULES:
        notes.extend(rule.check(article))
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
