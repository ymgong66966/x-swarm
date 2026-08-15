"""Pick what is worth an article this week.

Deterministic on purpose: the care stream publishes rarely and each piece is expensive
to review, so the selection rule should be explainable in one sentence — recent,
on-subject, authoritative where it claims to be, and not something we just published.
"""

from __future__ import annotations

import datetime as dt
import logging
import urllib.parse

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import STREAM_CARE, Candidate, Item
from ..sources import normalize_title
from .editor import NON_US_RE

log = logging.getLogger(__name__)

WEIGHTS = {
    "authority": 0.32,
    "subject_fit": 0.27,
    "freshness": 0.17,
    "novelty": 0.12,
    "us_fit": 0.12,
}

# Alverna sells into the United States, so a study of a health system nobody here works
# in is worth less than the same finding from a US cohort, and a CMS rule is worth most.
US_MARKERS = (
    "medicare",
    "medicaid",
    "cms",
    "united states",
    "u.s.",
    "us ",
    "american",
    "federal register",
    "hhs",
    "cpt",
    "hcpcs",
    "fee schedule",
    "medicare advantage",
    "veterans affairs",
)

# An item's evidence kind decides how far it can carry a piece on its own.
AUTHORITY = {"regulatory": 1.0, "research": 0.85, "press": 0.5, "signal": 0.25}

# Subject terms, weighted by how close they sit to what the company actually does.
SUBJECT_TERMS = {
    "caregiver training": 1.0,
    "caregiver": 0.9,
    "family caregiver": 0.9,
    "cts": 0.6,
    "97550": 1.0,
    "96202": 1.0,
    "g0541": 1.0,
    "telehealth": 0.7,
    "home health": 0.7,
    "discharge": 0.7,
    "readmission": 0.75,
    "dementia": 0.7,
    "physician fee schedule": 0.8,
    "medicare": 0.6,
    "occupational therap": 0.6,
    "physical therap": 0.6,
    "speech-language": 0.6,
    "fall prevention": 0.8,
    "activities of daily living": 0.8,
    "aging in place": 0.6,
    "care transition": 0.8,
}

# Which pillar a source naturally feeds, before the angle agent gets an opinion.
PILLAR_BY_SOURCE = {
    "federal_register": "policy_explainer",
    "pubmed": "transitions_of_care",
    "industry_press": "policy_explainer",
    "news": "policy_explainer",
    "forum": "field_signal",
}

AUDIENCE_TERMS = {
    "clinician": ("therapist", "clinician", "nurse", "pathologist", "psychologist", "license"),
    "caregiver": ("family", "caregiver", "loved one", "at home", "dementia", "spouse"),
    "provider": ("hospital", "health system", "provider", "billing", "reimbursement", "referral"),
}


def score_authority(item: Item) -> float:
    kind = str(item.signals.get("evidence_kind", "press"))
    base = AUTHORITY.get(kind, 0.4)
    host = urllib.parse.urlsplit(item.url).netloc.lower()
    if any(host.endswith(allowed) for allowed in settings.care_authoritative_hosts):
        base = max(base, 0.9)
    return base


def score_us_fit(item: Item) -> float:
    """How much this item is about the market we publish into.

    Not a filter: a foreign randomised trial can still be the best evidence for a
    mechanism, and the writer may use it with a label. It just should not out-rank a CMS
    rule for the front of the queue, which is what happened before this score existed.
    """
    text = f"{item.title} {item.summary}".lower()
    domestic = any(marker in text for marker in US_MARKERS)
    foreign = bool(NON_US_RE.search(text))
    if domestic and not foreign:
        return 1.0
    if domestic:
        return 0.6
    return 0.15 if foreign else 0.5


def score_subject_fit(item: Item) -> float:
    text = f"{item.title} {item.summary}".lower()
    matched = [weight for term, weight in SUBJECT_TERMS.items() if term in text]
    if not matched:
        return 0.0
    return min(1.0, sum(sorted(matched, reverse=True)[:3]) / 2.2)


def score_freshness(item: Item, today: dt.date) -> float:
    published = item.published_at
    if published is None:
        return 0.5
    age = (today - published.date()).days
    if age <= 7:
        return 1.0
    return max(0.0, 1.0 - age / settings.care_source_max_age_days)


def score_novelty(item: Item, history: list[str]) -> float:
    if not history:
        return 1.0
    title = normalize_title(item.title)
    closest = max(fuzz.token_set_ratio(title, past) for past in history)
    return max(0.0, 1.0 - closest / 100)


def infer_audience(item: Item) -> str:
    text = f"{item.title} {item.summary}".lower()
    hinted = str(item.signals.get("audience_hint", ""))
    counts = {
        audience: sum(term in text for term in terms) for audience, terms in AUDIENCE_TERMS.items()
    }
    if hinted in counts:
        counts[hinted] += 1
    best = max(counts, key=lambda key: counts[key])
    return best if counts[best] else "provider"


def _history(session: Session) -> list[str]:
    cutoff = dt.date.today() - dt.timedelta(days=settings.novelty_window_days)
    rows = session.execute(
        select(Item.title)
        .join(Candidate, Candidate.item_id == Item.id)
        .where(Candidate.stream == STREAM_CARE, Candidate.run_date >= cutoff)
    ).scalars()
    return [normalize_title(title) for title in rows]


def run(session: Session, run_date: dt.date | None = None) -> list[Candidate]:
    run_date = run_date or dt.date.today()
    history = _history(session)
    already = set(
        session.execute(
            select(Candidate.item_id).where(
                Candidate.stream == STREAM_CARE, Candidate.run_date == run_date
            )
        ).scalars()
    )
    items = [
        item
        for item in session.scalars(
            select(Item)
            .where(Item.stream == STREAM_CARE)
            .order_by(Item.created_at.desc())
            .limit(400)
        )
        if item.id not in already
    ]

    scored: list[tuple[float, dict[str, float], Item]] = []
    for item in items:
        subscores = {
            "authority": score_authority(item),
            "subject_fit": score_subject_fit(item),
            "freshness": score_freshness(item, run_date),
            "novelty": score_novelty(item, history),
            "us_fit": score_us_fit(item),
        }
        # Something we cannot tie to the subject is not worth an article, however loud.
        if subscores["subject_fit"] < 0.2:
            continue
        total = sum(WEIGHTS[key] * value for key, value in subscores.items())
        scored.append((total, subscores, item))

    scored.sort(key=lambda row: row[0], reverse=True)
    candidates: list[Candidate] = []
    for total, subscores, item in scored[: settings.care_candidates_per_run]:
        audience = infer_audience(item)
        candidate = Candidate(
            item_id=item.id,
            stream=STREAM_CARE,
            run_date=run_date,
            score=round(total, 4),
            subscores={**{k: round(v, 4) for k, v in subscores.items()}, "audience": audience},
            pillar=PILLAR_BY_SOURCE.get(item.source, "policy_explainer"),
            rationale=f"{item.signals.get('evidence_kind', 'press')} source for a {audience} piece",
        )
        session.add(candidate)
        candidates.append(candidate)
    session.flush()
    log.info("care curator kept %d of %d items", len(candidates), len(items))
    return candidates
