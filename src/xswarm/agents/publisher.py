from __future__ import annotations

import datetime as dt
import logging
import random
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Draft, Publication
from ..publishers import TypefullyClient

log = logging.getLogger(__name__)


def _slots(day: dt.date, tz: ZoneInfo) -> list[dt.datetime]:
    times = []
    for slot in settings.publish_slots:
        hour, minute = (int(part) for part in slot.split(":"))
        times.append(dt.datetime.combine(day, dt.time(hour, minute), tzinfo=tz))
    return times


SLOT_SPACING = dt.timedelta(minutes=45)


def next_slots(
    count: int,
    *,
    taken: list[dt.datetime],
    now: dt.datetime | None = None,
    rng: random.Random | None = None,
) -> list[dt.datetime]:
    """The next `count` free posting slots, jittered so the account never looks
    like a cron job. A slot is skipped when something is already queued near it —
    `taken` times are jittered too, so this compares by proximity, not equality."""
    tz = ZoneInfo(settings.publish_timezone)
    rng = rng or random.Random()
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    busy = list(taken)
    chosen: list[dt.datetime] = []
    day = now.date()
    while len(chosen) < count and day < now.date() + dt.timedelta(days=14):
        for slot in _slots(day, tz):
            if slot <= now or any(abs(slot - t) < SLOT_SPACING for t in busy):
                continue
            jitter = rng.randint(-settings.publish_jitter_minutes, settings.publish_jitter_minutes)
            chosen.append(slot + dt.timedelta(minutes=jitter))
            busy.append(slot)
            if len(chosen) == count:
                break
        day += dt.timedelta(days=1)
    return chosen


def queued_times(session: Session) -> list[dt.datetime]:
    """Times already on the queue, so a new post is never stacked on top of one."""
    rows = session.scalars(
        select(Publication.scheduled_for).where(
            Publication.status.in_(("scheduled", "planned", "pending"))
        )
    )
    # SQLite drops the offset, so a stored slot comes back as bare wall time in the
    # publishing timezone; labelling it UTC would put the spacing check hours out.
    tz = ZoneInfo(settings.publish_timezone)
    return [row if row.tzinfo else row.replace(tzinfo=tz) for row in rows if row is not None]


def _thread(draft: Draft) -> list[str]:
    posts = [draft.body, *(draft.thread or [])]
    if draft.link_reply:
        posts.append(draft.link_reply)
    return posts


def _title(draft: Draft) -> str:
    """Typefully's internal draft name. Roundups have no single source item."""
    if draft.brief:
        return draft.brief.candidate.item.title[:80]
    return str((draft.features or {}).get("topic") or draft.body[:80])


def publish(
    session: Session,
    draft: Draft,
    when: dt.datetime,
    *,
    client: TypefullyClient | None = None,
    plan_only: bool = True,
) -> Publication:
    """Hand one approved draft to Typefully. `plan_only` keeps it inert on the queue
    until a human confirms — phase-1 autonomy."""
    publication = draft.publication or Publication(draft_id=draft.id)
    publication.scheduled_for = when

    if client is None:
        publication.status = "pending"
        session.add(publication)
        log.info("dry run: would schedule draft %s for %s", draft.id, when.isoformat())
        return publication

    media_ids = [client.upload_media(Path(a.path)) for a in draft.assets if Path(a.path).exists()]
    response = client.create_draft(
        _thread(draft),
        media_ids=media_ids,
        publish_at=when,
        plan_only=plan_only,
        title=_title(draft),
    )
    publication.provider_draft_id = str(response.get("draft_id") or response.get("id") or "")
    publication.status = "planned" if plan_only else "scheduled"
    draft.status = "scheduled"
    session.add(publication)
    session.flush()
    log.info("draft %s -> typefully %s at %s", draft.id, publication.provider_draft_id, when)
    return publication


def run(
    session: Session,
    *,
    dry_run: bool = False,
    plan_only: bool = True,
    limit: int | None = None,
) -> list[Publication]:
    """Schedule everything a human approved (plus any pillar allowed to self-publish)."""
    approved = list(
        session.scalars(
            select(Draft)
            .where(Draft.status == "approved", Draft.publication == None)  # noqa: E711
            .order_by(Draft.created_at)
        )
    )
    if settings.autopublish_pillars:
        auto = session.scalars(
            select(Draft)
            .where(Draft.status == "ready_for_review", Draft.publication == None)  # noqa: E711
            .order_by(Draft.created_at)
        )
        approved += [d for d in auto if d.features.get("pillar") in settings.autopublish_pillars]
    if limit is not None:
        approved = approved[:limit]
    if not approved:
        log.info("nothing approved to publish")
        return []

    client = None if dry_run or not settings.typefully_api_key else TypefullyClient()
    slots = next_slots(len(approved), taken=queued_times(session))
    publications = [
        publish(session, draft, when, client=client, plan_only=plan_only)
        for draft, when in zip(approved, slots, strict=False)
    ]
    session.flush()
    log.info("scheduled %d posts", len(publications))
    return publications
