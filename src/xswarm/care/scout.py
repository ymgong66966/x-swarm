from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..agents.scout import gather, persist
from ..models import STREAM_CARE, Item
from ..sources.base import RawItem
from .sources import FETCHERS

log = logging.getLogger(__name__)


def collect(only: list[str] | None = None) -> list[RawItem]:
    return gather(FETCHERS, only=only)


def run(session: Session, only: list[str] | None = None) -> list[Item]:
    items = persist(session, collect(only=only), stream=STREAM_CARE)
    log.info("care scout stored %d new items", len(items))
    return items
