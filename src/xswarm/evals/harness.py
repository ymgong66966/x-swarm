"""Offline draft-quality evaluation.

The playbook rewrites itself from analytics, and the Writer prompt changes with it. That
loop can quietly get worse, and impressions take a week to tell you. This harness runs
the Writer and Editor over frozen briefs in a throwaway database and scores the output
against the rules we actually care about, so a prompt or playbook change can be judged in
seconds and before it ships.

Scores are deterministic — no LLM judge — because a judge that drifts cannot be a
regression test.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..agents import editor, writer
from ..config import settings
from ..llm import LLM
from ..models import Base, Brief, Candidate, Draft, Item

FIXTURES_PATH = Path(__file__).with_name("fixtures.json")

# Openings that waste the only eight words that decide whether anyone reads the post.
THROAT_CLEARING = (
    "interesting paper",
    "new paper",
    "this paper",
    "researchers have",
    "a new study",
    "i just read",
    "check out",
    "excited to share",
    "great read",
    "here is",
    "here's a",
)
IDEAL_CHARS = 220
VARIANT_SIMILARITY_LIMIT = 70


@dataclass
class DraftScore:
    slug: str
    variant: int
    body: str
    passed_editor: bool
    editor_notes: list[str]
    hook_ok: bool
    concise: bool
    specific: bool
    score: float


@dataclass
class EvalReport:
    scores: list[DraftScore] = field(default_factory=list)
    caveat_coverage: float = 0.0
    variant_diversity: float = 0.0
    provider: str = "dry-run"

    @property
    def pass_rate(self) -> float:
        return _mean([s.passed_editor for s in self.scores])

    @property
    def mean_score(self) -> float:
        return _mean([s.score for s in self.scores])

    @property
    def overall(self) -> float:
        """One number for CI. Editorial pass rate dominates; the rest are tie-breakers."""
        return round(
            100
            * (
                0.45 * self.pass_rate
                + 0.30 * self.mean_score
                + 0.15 * self.caveat_coverage
                + 0.10 * self.variant_diversity
            ),
            1,
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "overall": self.overall,
            "pass_rate": round(self.pass_rate, 3),
            "mean_score": round(self.mean_score, 3),
            "caveat_coverage": round(self.caveat_coverage, 3),
            "variant_diversity": round(self.variant_diversity, 3),
            "drafts": [
                {
                    "slug": s.slug,
                    "variant": s.variant,
                    "passed_editor": s.passed_editor,
                    "editor_notes": s.editor_notes,
                    "hook_ok": s.hook_ok,
                    "concise": s.concise,
                    "specific": s.specific,
                    "score": round(s.score, 3),
                    "body": s.body,
                }
                for s in self.scores
            ],
        }


def _mean(values: list[float] | list[bool]) -> float:
    return sum(float(v) for v in values) / len(values) if values else 0.0


def load_fixtures(path: Path | None = None) -> list[dict]:
    return json.loads((path or FIXTURES_PATH).read_text())


def _seed(session: Session, fixtures: list[dict]) -> dict[int, str]:
    """Insert the frozen briefs. Returns brief id -> fixture slug."""
    slugs: dict[int, str] = {}
    for fixture in fixtures:
        item = Item(
            source=fixture["source"],
            external_id=fixture["slug"],
            fingerprint=fixture["slug"],
            title=fixture["title"],
            url=fixture["url"],
            summary=fixture["summary"],
            authors=[],
            signals={},
            published_at=dt.datetime.now(dt.timezone.utc),
        )
        session.add(item)
        session.flush()
        candidate = Candidate(
            item_id=item.id,
            run_date=dt.date.today(),
            score=1.0,
            subscores={},
            pillar=fixture["pillar"],
            rationale="fixture",
        )
        session.add(candidate)
        session.flush()
        brief = Brief(
            candidate_id=candidate.id,
            whats_new=fixture["whats_new"],
            what_it_replaces=fixture["what_it_replaces"],
            key_number=fixture["key_number"],
            caveat=fixture["caveat"],
            builder_takeaway=fixture["builder_takeaway"],
            grounded_claims=fixture["grounded_claims"],
            unverified_claims=fixture["unverified_claims"],
            visual_hint=fixture["visual_hint"],
        )
        session.add(brief)
        session.flush()
        slugs[brief.id] = fixture["slug"]
    return slugs


def hook_ok(body: str) -> bool:
    opening = " ".join(body.strip().split()[:8]).lower()
    return bool(opening) and not any(opening.startswith(p) for p in THROAT_CLEARING)


def specific(body: str, brief: Brief) -> bool:
    """A post that carries no number and no named thing from the brief is filler."""
    if any(ch.isdigit() for ch in body):
        return True
    claims = " ".join(brief.grounded_claims or [])
    return fuzz.partial_ratio(body.lower(), claims.lower()) > 60


def _score_draft(draft: Draft, slug: str) -> DraftScore:
    brief = draft.brief
    body = draft.body
    checks = {
        "hook": hook_ok(body),
        "concise": 0 < len(body) <= IDEAL_CHARS,
        "specific": specific(body, brief) if brief else False,
    }
    return DraftScore(
        slug=slug,
        variant=draft.variant,
        body=body,
        passed_editor=draft.status == "ready_for_review",
        editor_notes=list(draft.editor_notes or []),
        hook_ok=checks["hook"],
        concise=checks["concise"],
        specific=checks["specific"],
        score=_mean(list(checks.values())),
    )


def _caveat_coverage(drafts: list[Draft]) -> float:
    """Per brief: did at least one variant carry the caveat? Honesty is the brand."""
    by_brief: dict[int, list[Draft]] = {}
    for draft in drafts:
        if draft.brief_id is not None:
            by_brief.setdefault(draft.brief_id, []).append(draft)
    covered = [
        any(fuzz.partial_ratio(d.body.lower(), (d.brief.caveat or "").lower()) > 55 for d in group)
        for group in by_brief.values()
        if group and group[0].brief and group[0].brief.caveat
    ]
    return _mean(covered)


def _variant_diversity(drafts: list[Draft]) -> float:
    """Fraction of briefs whose variants are genuinely different posts."""
    by_brief: dict[int, list[Draft]] = {}
    for draft in drafts:
        if draft.brief_id is not None:
            by_brief.setdefault(draft.brief_id, []).append(draft)
    distinct: list[bool] = []
    for group in by_brief.values():
        bodies = [d.body for d in group]
        pairs = [
            fuzz.token_set_ratio(a, b)
            for i, a in enumerate(bodies)
            for b in bodies[i + 1 :]
        ]
        distinct.append(max(pairs) < VARIANT_SIMILARITY_LIMIT if pairs else True)
    return _mean(distinct)


def run_eval(*, dry_run: bool = True, fixtures_path: Path | None = None) -> EvalReport:
    """Write and edit every fixture brief in a throwaway in-memory database."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    llm = LLM(dry_run=dry_run)
    try:
        slugs = _seed(session, load_fixtures(fixtures_path))
        briefs = [session.get(Brief, bid) for bid in slugs]
        drafts = writer.run(session, llm, [b for b in briefs if b])
        editor.run(session, llm, drafts)
        report = EvalReport(
            scores=[_score_draft(d, slugs[d.brief_id]) for d in drafts if d.brief_id in slugs],
            caveat_coverage=_caveat_coverage(drafts),
            variant_diversity=_variant_diversity(drafts),
            provider="dry-run" if llm.dry_run else str(llm.provider),
        )
        return report
    finally:
        session.close()
        engine.dispose()


def format_report(report: EvalReport) -> str:
    lines = [
        f"provider={report.provider} overall={report.overall}/100",
        f"editor pass rate: {report.pass_rate:.0%}",
        f"mean draft score:  {report.mean_score:.0%}",
        f"caveat coverage:   {report.caveat_coverage:.0%}",
        f"variant diversity: {report.variant_diversity:.0%}",
    ]
    for score in report.scores:
        flags = "".join(
            [
                "H" if score.hook_ok else "-",
                "C" if score.concise else "-",
                "S" if score.specific else "-",
                "E" if score.passed_editor else "-",
            ]
        )
        lines.append(f"  [{flags}] {score.slug}#{score.variant} {score.body[:70]!r}")
        if score.editor_notes:
            lines.append(f"        blocked: {'; '.join(score.editor_notes)[:100]}")
    lines.append(f"{settings.max_post_chars} char limit, {IDEAL_CHARS} char ideal")
    return "\n".join(lines)
