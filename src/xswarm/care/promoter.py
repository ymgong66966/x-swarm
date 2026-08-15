"""Turn a cleared article into the posts that distribute it.

Promos are ordinary `Draft` rows with `stream="care"`, so they inherit the publisher,
the scheduler and the metrics pipeline the ML stream already uses — and land in the
same dashboard, which is the whole point of keeping one drafts table.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from ..config import settings
from ..llm import LLM, load_prompt
from ..models import STREAM_CARE, Article, Asset, Draft
from .editor import CLINICAL_DIRECTIVE_RE, NON_US_RE, banned_phrase_hits, promises_outcome

log = logging.getLogger(__name__)

ANGLES = ["finding", "consequence", "objection"]
# An instruction to the reader at the start of a sentence. A post that ends "Ask your
# provider about eligibility" is an ad; the same post without that line is someone
# sharing what they found, and the attached link is the only invitation needed.
CTA_RE = re.compile(
    r"(?:^|[.!?\n]\s*)(?:always |be sure to |make sure to |remember to )?"
    r"(?:ask|request|contact|confirm|check|reach out|talk to|book|schedule|"
    r"learn more|read more|find out|discover|explore|see how|get started|sign up|"
    r"don'?t miss)\b",
    re.IGNORECASE,
)
# A hook only works if the eye can find it. Everything the model produced before this
# check arrived as one unbroken block, which is the shape of a press release.
DENSE_CHARS = 150
# A hook with a single line under it is a post nobody stops for. There is room for the
# substance that earns the click, so the post is expected to use most of it.
MIN_POST_CHARS = 190
MIN_LINKEDIN_CHARS = 400
# The em dash is the most recognisable tell of generated prose. A person writing on a
# phone types a comma, or starts a new sentence.
DASH_RE = re.compile(r" *(?:[—–]|(?<= )--(?= )) *")
MARKETING_TELLS = (
    "unlock",
    "empower",
    "leverage",
    "seamless",
    "streamline",
    "game changer",
    "navigate the complexities",
    "in today's landscape",
    "signals the need",
    "key to success",
    "solutions",
)
TAKEAWAY_RE = re.compile(r"^- (.+)$", re.MULTILINE)
BANNED_SOCIAL = ("#", "🧵", "🚨")
# Where a reader of each audience should end up if the article did its job. The article
# itself is not the destination: these are the pages with a form on them.
LANDING_PATHS = {"provider": "/providers", "trainer": "/trainers", "caregiver": "/"}
LANDING_LABELS = {
    "provider": "How providers deliver and bill caregiver training",
    "trainer": "Clinicians who teach these sessions",
    "caregiver": "What Alverna does",
}


def tag(url: str, *, campaign: str, content: str, source: str = "x") -> str:
    """Stamp an outbound link with UTM parameters.

    Nothing reads these yet — site analytics is deliberately off. They cost nothing to
    add and cannot be added retroactively: a link posted untagged in August is
    unattributable forever, so every link ships tagged from the first post.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(
        {
            "utm_source": source,
            "utm_medium": "social",
            "utm_campaign": campaign,
            "utm_content": content,
        }
    )
    return urlunsplit(parts._replace(query=urlencode(query)))


def article_url(article: Article) -> str:
    if article.published_url:
        return article.published_url
    return f"{settings.care_blog_base_url.rstrip('/')}/{article.slug}"


def landing_url(audience: str) -> str:
    path = LANDING_PATHS.get(audience, "/")
    return f"{settings.care_site_url.rstrip('/')}{path}"


def link_reply(article: Article, *, channel: str, variant: int) -> str:
    """The trailing reply of a promo: the article, then the page that can convert.

    Links never go in the post itself — X suppresses reach on link posts — and the reply
    carries two of them because an article read with no route to `/providers` or
    `/trainers` is attention we cannot use.
    """
    content = f"{article.slug}-v{variant}"
    source = "linkedin" if channel == "linkedin" else "x"
    piece = tag(article_url(article), campaign="care_article", content=content, source=source)
    landing = tag(
        landing_url(article.audience), campaign="care_landing", content=content, source=source
    )
    label = LANDING_LABELS.get(article.audience, LANDING_LABELS["caregiver"])
    return f"Full piece: {piece}\n\n{label}: {landing}"


def hero_file(article: Article) -> Path | None:
    """The banner photograph on disk, if it is still there to upload."""
    if not article.hero_path:
        return None
    path = Path(article.hero_path)
    return path if path.is_file() else None


def attach_hero(session: Session, article: Article) -> int:
    """Give every promo the article's own photograph.

    The same image on the post and the page is the point: it is what a reader recognises
    when the link opens, and a post with an image is not the same object on the timeline
    as a post without one.
    """
    path = hero_file(article)
    if path is None:
        return 0
    attached = 0
    for draft in article.promos:
        if any(asset.path == str(path) for asset in draft.assets):
            continue
        draft.assets.append(
            Asset(
                kind="hero",
                path=str(path),
                alt_text=article.hero_alt or article.title,
                spec={"article_id": article.id},
            )
        )
        attached += 1
    session.flush()
    return attached


def release(session: Session, article: Article) -> int:
    """Prepare an article's promos for scheduling, once its URL is real.

    Re-stamps the links (the live URL may differ from the one guessed at write time) and
    attaches the hero, then approves everything the editor cleared.
    """
    for draft in article.promos:
        draft.link_reply = link_reply(
            article, channel=str(draft.features.get("channel", "x")), variant=draft.variant
        )
    attach_hero(session, article)
    approved = [d for d in article.promos if d.status == "ready_for_review"]
    for draft in approved:
        draft.status = "approved"
    session.flush()
    return len(approved)


def _takeaways(article: Article) -> list[str]:
    section = article.body_md.split("## Key takeaways", 1)
    if len(section) < 2:
        return []
    return TAKEAWAY_RE.findall(section[1].split("##", 1)[0])[:5]


def _clean(body: str) -> str:
    """Tidy whitespace without flattening the post.

    Line breaks are load-bearing: a hook line followed by the substance reads like a
    person, the same words run into one paragraph read like a press release. Em dashes
    are removed for the opposite reason: nothing else marks a post as machine-written
    as reliably, and a comma carries the same clause.
    """
    for token in BANNED_SOCIAL:
        body = body.replace(token, "")
    body = DASH_RE.sub(", ", body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r" *\n *", "\n", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _break_hook(body: str) -> str:
    """Put the opening sentence on its own line when the model forgot to.

    Purely typographic — no word changes — and it is the difference between a post that
    opens with a hook and a paragraph that happens to start with a question.
    """
    if "\n" in body or len(body) <= DENSE_CHARS:
        return body
    match = re.search(r"^(.{15,120}?[.!?])\s+(\S)", body, re.DOTALL)
    if match is None:
        return body
    return f"{match.group(1)}\n\n{body[match.start(2) :]}"


def _us_first(claims: list[str]) -> list[str]:
    """Order the facts the model may use so US evidence is the material it reaches for.

    The scout still collects non-US research (a caregiver-burden effect is a caregiver-
    burden effect), but the reader is a US clinician or family dealing with Medicare, so
    a foreign cohort is the fact of last resort rather than the headline.
    """
    domestic = [claim for claim in claims if not NON_US_RE.search(claim)]
    foreign = [claim for claim in claims if NON_US_RE.search(claim)]
    return domestic + foreign


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
            evidence="\n".join(f"- {claim}" for claim in _us_first(article.evidence)[:10])
            or "- (none)",
            variants=settings.care_promos_per_article,
            max_chars=settings.max_post_chars,
            min_chars=MIN_POST_CHARS,
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
            body = _break_hook(_clean(str(entry.get("body", ""))))
            if body:
                posts.append((body, str(entry.get("angle", ANGLES[index % 3]))))
    if not posts:
        posts = _fallback_posts(article)

    drafts: list[Draft] = []
    for index, (body, angle) in enumerate(posts[: settings.care_promos_per_article]):
        drafts.append(
            Draft(
                article_id=article.id,
                stream=STREAM_CARE,
                variant=index,
                body=body,
                link_reply=link_reply(article, channel="x", variant=index),
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
                link_reply=link_reply(article, channel="linkedin", variant=len(drafts)),
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


KEEP_STATUS = {"approved", "scheduled"}
VOICE_ATTEMPTS = 3


def _best_effort(article: Article, llm: LLM, attempts: int = VOICE_ATTEMPTS) -> list[Draft]:
    """Generate promos until they clear the checks, then keep the cleanest attempt.

    The voice rules are the kind a model breaks once and then gets right when asked
    again, so a retry is cheaper than a human rewriting the post by hand.
    """
    best: tuple[int, list[Draft]] | None = None
    for _ in range(max(1, attempts)):
        candidates = write(article, llm)
        flaws = sum(len(check(draft)) for draft in candidates)
        if best is None or flaws < best[0]:
            best = (flaws, candidates)
        if flaws == 0:
            break
    assert best is not None
    return best[1]


def rewrite(session: Session, article: Article, llm: LLM) -> list[Draft]:
    """Re-write an article's promos in place, keeping the rows.

    Not `write()` again into new rows: a promo that is already on the Typefully queue
    owns a publication and a slot, and both are addressed by draft id. Replacing the row
    would strand the queued post with the old copy forever. So the text changes and the
    identity does not — `xswarm requeue` then pushes the new words over the queued ones.
    """
    fresh = _best_effort(article, llm)
    by_channel: dict[str, list[Draft]] = {}
    for draft in fresh:
        by_channel.setdefault(str(draft.features.get("channel", "x")), []).append(draft)

    changed: list[Draft] = []
    for existing in sorted(article.promos, key=lambda d: d.variant):
        publication = existing.publication
        if publication is not None and (
            publication.status == "published" or publication.published_at
        ):
            log.info("draft %s is already published; leaving it alone", existing.id)
            continue
        channel = str(existing.features.get("channel", "x"))
        queue = by_channel.get(channel) or []
        if not queue:
            continue
        replacement = queue.pop(0)
        existing.body = replacement.body
        existing.features = {**existing.features, "hook_style": replacement.features["hook_style"]}
        notes = check(existing)
        existing.editor_notes = notes
        queued = publication is not None and bool(publication.provider_draft_id)
        if queued:
            # It is on the provider's queue whatever we think of the new words; moving it
            # out of "scheduled" would only make the local state lie about that.
            pass
        elif notes:
            existing.status = "blocked"
        elif existing.status not in KEEP_STATUS:
            existing.status = "ready_for_review"
        changed.append(existing)
    session.flush()
    log.info("care promos: %d rewritten for article %s", len(changed), article.id)
    return changed


def check(draft: Draft) -> list[str]:
    """Social copy gets the same safety floor as the article, at post length.

    Plus a voice floor. The first promos we wrote were safe and sourced and still read
    like a brochure — a dense claim followed by an instruction to the reader. Those two
    shapes are cheap to detect, so the model does not get to ship them.
    """
    notes: list[str] = []
    linkedin = draft.features.get("channel") == "linkedin"
    limit = 900 if linkedin else settings.max_post_chars
    floor = MIN_LINKEDIN_CHARS if linkedin else MIN_POST_CHARS
    if not draft.body.strip():
        notes.append("empty post")
    if len(draft.body) > limit:
        notes.append(f"{len(draft.body)} chars over the {limit} limit")
    elif draft.body.strip() and len(draft.body) < floor:
        notes.append(f"{len(draft.body)} chars: a hook with nothing under it, aim for {floor}+")
    for phrase in banned_phrase_hits(draft.body):
        notes.append(f"banned phrase: {phrase!r}")
    if promises_outcome(draft.body):
        notes.append("promises an outcome")
    if CLINICAL_DIRECTIVE_RE.search(draft.body):
        notes.append("personalised clinical direction")
    if any(token in draft.body for token in BANNED_SOCIAL):
        notes.append("hashtag or emoji noise")
    if len(draft.body) > DENSE_CHARS and "\n" not in draft.body.strip():
        notes.append("one dense block; the hook needs its own line")
    if CTA_RE.search(draft.body):
        notes.append("ends on a call to action; the link already does that")
    if DASH_RE.search(draft.body):
        notes.append("em dash: the clearest tell of generated prose")
    foreign = NON_US_RE.search(draft.body)
    if foreign:
        notes.append(f"leads on non-US evidence: {foreign.group(0)!r}")
    for phrase in MARKETING_TELLS:
        if phrase in draft.body.lower():
            notes.append(f"marketing filler: {phrase!r}")
    return notes


def run(session: Session, llm: LLM, articles: list[Article]) -> list[Draft]:
    drafts: list[Draft] = []
    for article in articles:
        for draft in _best_effort(article, llm):
            notes = check(draft)
            draft.editor_notes = notes
            draft.status = "blocked" if notes else "ready_for_review"
            session.add(draft)
            drafts.append(draft)
    session.flush()
    ready = sum(1 for d in drafts if d.status == "ready_for_review")
    log.info("care promos: %d written, %d ready", len(drafts), ready)
    return drafts
