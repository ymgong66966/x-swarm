"""Token accounting.

The budget for this account is ~$30/mo including Typefully, so model spend has to be
visible per agent — a single runaway stage (the Curator scores every item) is the
realistic way to blow it, and an aggregate number would not show that.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .llm import LLM
from .models import ModelCall

log = logging.getLogger(__name__)


def record(session: Session, llm: LLM, *, run_date: dt.date | None = None) -> float:
    """Drain the LLM's usage log into the database. Returns the dollars recorded."""
    run_date = run_date or dt.date.today()
    total = 0.0
    for usage in llm.usage:
        session.add(
            ModelCall(
                run_date=run_date,
                agent=usage.agent,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=usage.cost_usd,
            )
        )
        total += usage.cost_usd
    llm.usage.clear()
    session.flush()
    return total


def by_agent(session: Session, *, days: int = 30) -> list[tuple[str, int, int, int, float]]:
    """(agent, calls, prompt tokens, completion tokens, usd), priciest first."""
    cutoff = dt.date.today() - dt.timedelta(days=days)
    rows = session.execute(
        select(
            ModelCall.agent,
            func.count(ModelCall.id),
            func.sum(ModelCall.prompt_tokens),
            func.sum(ModelCall.completion_tokens),
            func.sum(ModelCall.cost_usd),
        )
        .where(ModelCall.run_date >= cutoff)
        .group_by(ModelCall.agent)
    ).all()
    summary = [(a, int(c), int(p or 0), int(o or 0), float(u or 0.0)) for a, c, p, o, u in rows]
    return sorted(summary, key=lambda row: row[4], reverse=True)


def month_to_date(session: Session) -> float:
    today = dt.date.today()
    total = session.scalar(
        select(func.sum(ModelCall.cost_usd)).where(ModelCall.run_date >= today.replace(day=1))
    )
    return float(total or 0.0)


def projected_month(session: Session) -> float:
    """Month-to-date extrapolated to the full month, for the budget warning."""
    today = dt.date.today()
    days_in_month = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(
        days=1
    )
    return month_to_date(session) / today.day * days_in_month.day


def over_budget(session: Session) -> bool:
    return projected_month(session) > settings.monthly_budget_usd
