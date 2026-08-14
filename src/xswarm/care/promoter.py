"""Turn a cleared article into the posts that distribute it.

Promos are ordinary `Draft` rows with `stream="care"`, so they inherit the publisher,
the scheduler and the metrics pipeline the ML stream already uses — and land in the
same dashboard, which is the whole point of keeping one drafts table.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import STREAM_CARE, Article, Draft
from .editor import CLINICAL_DIRECTIVE_RE, banned_phrase_hits, promises_outcome

log = logging.getLogger(__name__)

ANGLES = ["finding", "consequence", "objection"]
TAKEAWAY_RE = re.compile(r"^- (.+)$", re.MULTILINE)
BANNED_SOCIAL = ("#", "🧵", "🚨")


def article_url(article: Article) -> str:
    if article.published_url:
        return article.published_url
    return f"{settings.care_blog_base_url.rstrip('/')}/{article.slug}"


def _takeaways(article: Article) -> list[str]:
    section = article.body_md.split("## Key takeaways", 1)
    if len(section) < 2:
        return []
    return TAKEAWAY_RE.findall(section[1].split("##", 1)[0])[:5]


def _clean(body: str) -> str:
    for token in BANNED_SOCIAL:
        body = body.replace(token, "")
    return re.sub(r"\s+", " ", body).strip()


def _fallback_posts(article: Article) -> list[tuple[str, str]]:
    """Without a model the thesis is still a true, sourced sentence, so it can carry one
    post. Anything more would be invention."""
    return [(_clean(article.thesis)[: settings.max_post_chars], "finding")]


def write(article: Article, llm: LLM) -> list[Draft]:
    takeaways = _takeaways(article)
    payload = llm.complete_json(
        load_prompt("care_promo").format(
            audience=article.audience,
            title=article.title,
            thesis=article.thesis,
            takeaways="\n".join(f"- {line}" for line in takeaways) or f"- {article.thesis}",
            evidence="\n".join(f"- {claim}" for claim in article.evidence[:10]) or "- (none)",
            variants=settings.care_promos_per_article,
            max_chars=settings.max_post_chars,
        ),
        strong=True,
        max_tokens=1200,
        agent="care_promoter",
    )

    posts: list[tuple[str, str]] = []
    linkedin = ""
    if isinstance(payload, dict):
        linkedin = _clean(str(payload.get("linkedin", "")))
        for index, entry in enumerate(payload.get("x_posts", [])):
            if not isinstance(entry, dict):
                continue
            body = _clean(str(entry.get("body", "")))
            if body:
                posts.append((body, str(entry.get("angle", ANGLES[index % 3]))))
    if not posts:
        posts = _fallback_posts(article)

    link = article_url(article)
    drafts: list[Draft] = []
    for index, (body, angle) in enumerate(posts[: settings.care_promos_per_article]):
        drafts.append(
            Draft(
                article_id=article.id,
                stream=STREAM_CARE,
                variant=index,
                body=body,
                link_reply=f"Full piece: {link}",
                features={
                    "hook_style": angle,
                    "pillar": article.pillar,
                    "audience": article.audience,
                    "channel": "x",
                    "article_slug": article.slug,
                },
            )
        )
    if linkedin:
        drafts.append(
            Draft(
                article_id=article.id,
                stream=STREAM_CARE,
                variant=len(drafts),
                body=linkedin,
                link_reply=link,
                features={
                    "hook_style": "linkedin",
                    "pillar": article.pillar,
                    "audience": article.audience,
                    "channel": "linkedin",
                    "article_slug": article.slug,
                },
            )
        )
    return drafts


def check(draft: Draft) -> list[str]:
    """Social copy gets the same safety floor as the article, at post length."""
    notes: list[str] = []
    limit = 900 if draft.features.get("channel") == "linkedin" else settings.max_post_chars
    if not draft.body.strip():
        notes.append("empty post")
    if len(draft.body) > limit:
        notes.append(f"{len(draft.body)} chars over the {limit} limit")
    for phrase in banned_phrase_hits(draft.body):
        notes.append(f"banned phrase: {phrase!r}")
    if promises_outcome(draft.body):
        notes.append("promises an outcome")
    if CLINICAL_DIRECTIVE_RE.search(draft.body):
        notes.append("personalised clinical direction")
    if any(token in draft.body for token in BANNED_SOCIAL):
        notes.append("hashtag or emoji noise")
    return notes


def run(session: Session, llm: LLM, articles: list[Article]) -> list[Draft]:
    drafts: list[Draft] = []
    for article in articles:
        for draft in write(article, llm):
            notes = check(draft)
            draft.editor_notes = notes
            draft.status = "blocked" if notes else "ready_for_review"
            session.add(draft)
            drafts.append(draft)
    session.flush()
    ready = sum(1 for d in drafts if d.status == "ready_for_review")
    log.info("care promos: %d written, %d ready", len(drafts), ready)
    return drafts
