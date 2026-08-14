"""Write the article the plan committed to."""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import Article
from . import site
from .angle import Plan

log = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z0-9'-]+")


def word_count(markdown: str) -> int:
    return len(_WORD.findall(markdown))


def _takeaways_block(takeaways: list[str]) -> str:
    if not takeaways:
        return ""
    lines = "\n".join(f"- {line}" for line in takeaways)
    return f"## Key takeaways\n\n{lines}\n\n"


def _faq_block(faq: list[dict[str, str]]) -> str:
    if not faq:
        return ""
    parts = ["## Frequently asked questions\n"]
    for entry in faq:
        question = str(entry.get("q", "")).strip()
        answer = str(entry.get("a", "")).strip()
        if question and answer:
            parts.append(f"**{question}**\n\n{answer}\n")
    return "\n".join(parts) + "\n" if len(parts) > 1 else ""


def _sources_block(plan: Plan) -> str:
    citable = [e for e in plan.evidence if e.kind != "product"]
    if not citable:
        return ""
    lines = "\n".join(f"- [{e.title}]({e.url}) — {e.kind}" for e in citable)
    return f"## Sources\n\n{lines}\n"


def compose(plan: Plan, body_md: str, takeaways: list[str], faq: list[dict[str, str]]) -> str:
    """Assemble the published shape: article, takeaways, FAQ, disclaimer, sources."""
    return (
        f"{body_md.strip()}\n\n"
        f"{_takeaways_block(takeaways)}"
        f"{_faq_block(faq)}"
        f"_{settings.care_disclaimer}_\n\n"
        f"{_sources_block(plan)}"
    )


def write(session: Session, plan: Plan, llm: LLM, run_date: dt.date) -> Article:
    facts = site.context(session, plan.audience)
    payload = llm.complete_json(
        load_prompt("care_article").format(
            audience=plan.audience,
            pillar=plan.pillar,
            thesis=plan.thesis,
            title=plan.title,
            dek=plan.dek,
            outline="\n".join(f"- {section}" for section in plan.outline) or "- (no outline)",
            evidence="\n".join(
                f"- [{e.kind}] {e.claim} ({e.url})" for e in plan.evidence if e.kind != "product"
            )
            or "- (no evidence)",
            product_facts="\n".join(f"- {fact.text}" for fact in facts[:25]) or "- (none)",
            min_words=settings.care_min_words,
            max_words=settings.care_max_words,
        ),
        strong=True,
        max_tokens=6000,
        agent="care_writer",
    )

    if isinstance(payload, dict) and str(payload.get("body_md", "")).strip():
        title = str(payload.get("title") or plan.title).strip()[:140]
        dek = str(payload.get("dek") or plan.dek).strip()
        meta = str(payload.get("meta_description") or plan.meta_description).strip()[:160]
        takeaways = [str(t) for t in payload.get("key_takeaways", [])][:5]
        faq_raw = payload.get("faq", [])
        faq = [dict(entry) for entry in faq_raw if isinstance(entry, dict)][:5]
        body = compose(plan, str(payload["body_md"]), takeaways, faq)
    else:
        # No model: record the plan so the run is auditable, and let the editor stop it.
        title, dek, meta = plan.title, plan.dek, plan.meta_description
        body = compose(plan, "Drafted without model access; not written.", [], [])

    final = dataclasses.replace(plan, title=title)
    article = Article(
        run_date=run_date,
        pillar=plan.pillar,
        audience=plan.audience,
        thesis=plan.thesis,
        title=title,
        slug=final.slug,
        dek=dek,
        meta_description=meta,
        keywords=plan.keywords,
        outline=plan.outline,
        body_md=body,
        word_count=word_count(body),
        sources=[e.as_source() for e in plan.evidence],
        evidence=[e.claim for e in plan.evidence],
    )
    session.add(article)
    session.flush()
    log.info("wrote article %s (%d words)", article.slug, article.word_count)
    return article
