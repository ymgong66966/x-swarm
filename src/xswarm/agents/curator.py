from __future__ import annotations

import datetime as dt
import logging
import math

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import memory
from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Candidate, Item
from ..sources import normalize_title

log = logging.getLogger(__name__)

WEIGHTS = {
    "momentum": 0.30,
    "relevance": 0.30,
    "credibility": 0.15,
    "novelty": 0.15,
    "visualizability": 0.10,
}

PILLAR_BY_SOURCE = {
    "github_trending": "systems_take",
    "github_release": "systems_take",
    "newsletter": "curation",
}

# Topics we care about, used for the keyword fallback when the LLM is unavailable.
TOPIC_KEYWORDS = {
    "agent": 1.0,
    "multi-agent": 1.0,
    "tool use": 0.9,
    "rag": 0.8,
    "retrieval": 0.7,
    "inference": 0.8,
    "serving": 0.8,
    "latency": 0.7,
    "reasoning": 0.8,
    "evaluation": 0.7,
    "benchmark": 0.5,
    "fine-tun": 0.6,
    "distillation": 0.6,
    "quantization": 0.6,
    "context": 0.6,
    "memory": 0.7,
    "reinforcement learning": 0.7,
}


def score_momentum(item: Item) -> float:
    """Social/community attention, log-compressed so one viral paper cannot dominate."""
    upvotes = float(item.signals.get("hf_upvotes", 0) or 0)
    stars = float(item.signals.get("stars", 0) or 0)
    also_seen = len(item.signals.get("also_seen_in", []))
    raw = math.log1p(upvotes) / math.log(200) + math.log1p(stars) / math.log(20000)
    return min(1.0, raw + 0.15 * also_seen)


def score_credibility(item: Item) -> float:
    citations = float(item.signals.get("citations", 0) or 0)
    h_index = float(item.signals.get("max_author_h_index", 0) or 0)
    venue = 0.15 if item.signals.get("venue") else 0.0
    return min(1.0, math.log1p(citations) / math.log(500) + min(h_index, 60) / 120 + venue)


def score_novelty(item: Item, history: list[str]) -> float:
    """1.0 when we have never covered anything like this in the novelty window."""
    title = normalize_title(item.title)
    if not history:
        return 1.0
    closest = max(fuzz.token_set_ratio(title, past) for past in history)
    return max(0.0, 1.0 - closest / 100)


def score_visualizability(item: Item) -> float:
    """Cheap proxy for 'can the Visualizer make one honest image out of this'."""
    text = f"{item.title} {item.summary}".lower()
    cues = ("architecture", "pipeline", "benchmark", "ablation", "%", "speedup", "x faster")
    hits = sum(cue in text for cue in cues)
    return min(1.0, 0.3 + 0.15 * hits)


def score_relevance(item: Item, llm: LLM) -> tuple[float, str]:
    scored = llm.complete_json(
        load_prompt("curator").format(
            title=item.title,
            summary=item.summary[:1500],
            source=item.source,
        ),
        agent="curator",
    )
    if isinstance(scored, dict) and "relevance" in scored:
        return float(scored["relevance"]), str(scored.get("rationale", ""))
    return _keyword_relevance(item), "keyword fallback (no LLM)"


def _keyword_relevance(item: Item) -> float:
    text = f"{item.title} {item.summary}".lower()
    matched = [weight for keyword, weight in TOPIC_KEYWORDS.items() if keyword in text]
    if not matched:
        return 0.2
    return min(1.0, sum(sorted(matched, reverse=True)[:3]) / 2.4)


def _recent_titles(session: Session) -> list[str]:
    cutoff = dt.date.today() - dt.timedelta(days=settings.novelty_window_days)
    rows = session.execute(
        select(Item.title).join(Candidate, Candidate.item_id == Item.id).where(
            Candidate.run_date >= cutoff
        )
    ).scalars()
    return [normalize_title(t) for t in rows]


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite round-trips datetimes without tzinfo; normalise before comparing."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=dt.timezone.utc)


def _pillar(item: Item) -> str:
    return PILLAR_BY_SOURCE.get(item.source, "paper_of_the_day")


def run(session: Session, llm: LLM, run_date: dt.date | None = None) -> list[Candidate]:
    run_date = run_date or dt.date.today()
    history = _recent_titles(session)
    covered = memory.covered_titles(session)
    already_scored = set(
        session.execute(
            select(Candidate.item_id).where(Candidate.run_date == run_date)
        ).scalars()
    )
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    items = [
        item
        for item in session.scalars(select(Item).order_by(Item.created_at.desc()).limit(400))
        if item.id not in already_scored
        and (item.published_at is None or _as_utc(item.published_at) >= cutoff)
    ]

    scored: list[tuple[float, dict, str, Item]] = []
    for item in items:
        # Cheaper than an LLM call, and a topic we already posted cannot win today.
        if memory.is_repeat(item.title, covered):
            continue
        relevance, rationale = score_relevance(item, llm)
        subscores = {
            "momentum": score_momentum(item),
            "relevance": relevance,
            "credibility": score_credibility(item),
            "novelty": score_novelty(item, history),
            "visualizability": score_visualizability(item),
        }
        total = sum(WEIGHTS[k] * v for k, v in subscores.items())
        # A topic we just covered is worth less than a fresh one regardless of quality.
        if subscores["novelty"] < 0.35:
            total *= 0.4
        scored.append((total, subscores, rationale, item))

    scored.sort(key=lambda row: row[0], reverse=True)
    candidates: list[Candidate] = []
    for total, subscores, rationale, item in scored[: settings.candidates_per_day]:
        candidate = Candidate(
            item_id=item.id,
            run_date=run_date,
            score=round(total, 4),
            subscores={k: round(v, 4) for k, v in subscores.items()},
            pillar=_pillar(item),
            rationale=rationale,
        )
        session.add(candidate)
        candidates.append(candidate)
    session.flush()
    log.info("curated %d candidates from %d items", len(candidates), len(items))
    return candidates
