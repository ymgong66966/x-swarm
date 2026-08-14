"""Turn a lead item plus its supporting material into one arguable point.

The user-visible requirement is "theme or point centric articles that make clear
points". That is a planning problem, not a writing problem, so it gets its own step:
before anything is written, the stream commits to a thesis, an audience, an outline,
and the exact evidence each section is allowed to lean on.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Candidate, Item, SiteFact
from . import site
from .curator import infer_audience, score_subject_fit

log = logging.getLogger(__name__)

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class Evidence:
    """One citable fact. `kind` decides what the editor will let it support."""

    claim: str
    url: str
    title: str
    kind: str  # regulatory | research | press | signal | product

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).netloc.lower()

    @property
    def is_authoritative(self) -> bool:
        return any(self.host.endswith(h) for h in settings.care_authoritative_hosts)

    def as_source(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title, "kind": self.kind, "publisher": self.host}


@dataclass(slots=True)
class Plan:
    audience: str
    pillar: str
    thesis: str
    title: str
    dek: str
    meta_description: str
    outline: list[str]
    keywords: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return _SLUG.sub("-", self.title.lower()).strip("-")[:120]


def supporting(session: Session, lead: Candidate, pool: list[Candidate]) -> list[Item]:
    """Other items from this run that argue the same point, best fit first."""
    others = [c.item for c in pool if c.id != lead.id]
    others.sort(key=score_subject_fit, reverse=True)
    return others[: settings.care_evidence_per_article]


def _evidence_from_items(lead: Item, support: list[Item]) -> list[Evidence]:
    """The deterministic floor: every source we handed the model, as its own citation."""
    evidence: list[Evidence] = []
    for item in [lead, *support]:
        kind = str(item.signals.get("evidence_kind", "press"))
        evidence.append(Evidence(claim=item.title, url=item.url, title=item.title, kind=kind))
    return evidence


def _evidence_from_facts(facts: list[SiteFact]) -> list[Evidence]:
    return [
        Evidence(claim=fact.text, url=fact.url, title=fact.section or "Alverna", kind="product")
        for fact in facts
    ]


def _fallback(lead: Candidate, evidence: list[Evidence], audience: str) -> Plan:
    """No model: still produce a plan, but one the editor will hold back, because an
    unargued summary is exactly the kind of content this stream exists not to publish."""
    title = lead.item.title[:120]
    return Plan(
        audience=audience,
        pillar=lead.pillar,
        thesis="",
        title=title,
        dek="",
        meta_description="",
        outline=[],
        keywords=[],
        evidence=evidence,
    )


def plan(
    session: Session,
    lead: Candidate,
    pool: list[Candidate],
    llm: LLM,
) -> Plan:
    audience = str(lead.subscores.get("audience") or infer_audience(lead.item))
    support = supporting(session, lead, pool)
    facts = site.context(session, audience)
    evidence = _evidence_from_items(lead.item, support) + _evidence_from_facts(facts)

    payload = llm.complete_json(
        load_prompt("care_angle").format(
            audience=audience,
            pillar=lead.pillar,
            pillars=", ".join(settings.care_pillars),
            lead_title=lead.item.title,
            lead_url=lead.item.url,
            lead_kind=lead.item.signals.get("evidence_kind", "press"),
            lead_summary=lead.item.summary[:3000],
            support="\n".join(
                f"- [{i.signals.get('evidence_kind', 'press')}] {i.title} ({i.url})"
                for i in support
            )
            or "- (nothing else this run)",
            product_facts="\n".join(f"- {fact.text}" for fact in facts[:25])
            or "- (site not synced)",
        ),
        strong=True,
        max_tokens=1600,
        agent="care_angle",
    )
    if not isinstance(payload, dict) or not str(payload.get("thesis", "")).strip():
        return _fallback(lead, evidence, audience)

    pillar = str(payload.get("pillar", lead.pillar))
    return Plan(
        audience=str(payload.get("audience", audience)),
        pillar=pillar if pillar in settings.care_pillars else lead.pillar,
        thesis=str(payload["thesis"]).strip(),
        title=str(payload.get("title", lead.item.title)).strip()[:140],
        dek=str(payload.get("dek", "")).strip(),
        meta_description=str(payload.get("meta_description", "")).strip()[:160],
        outline=[str(section) for section in payload.get("outline", [])][:8],
        keywords=[str(keyword) for keyword in payload.get("keywords", [])][:8],
        evidence=evidence,
    )
