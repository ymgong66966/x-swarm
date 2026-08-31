"""Shared pipeline infrastructure for both the ML and care streams.

Each stream has its own graph topology and its own agents, but both need the same
session-per-node pattern, cost tracking, and run lifecycle. This module holds those
shared pieces so neither graph file has to duplicate them.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from . import costs
from .db import session_scope
from .llm import LLM
from .models import PipelineRun, utcnow

log = logging.getLogger(__name__)


def _last(_left: Any, right: Any) -> Any:
    """LangGraph reducer: last write wins."""
    return right


def make_llm(state: dict[str, Any]) -> LLM:
    return LLM(dry_run=state.get("dry_run", False))


def spend(session: Session, llm: LLM, state: dict[str, Any]) -> float:
    """Persist this node's model usage and keep the running total on the state."""
    return state.get("cost_usd", 0.0) + costs.record(
        session, llm,
        run_date=state["run_date"],
        pipeline_run_id=state.get("pipeline_run_id"),
    )


def start_run(stream: str, run_date: dt.date) -> int:
    """Open a PipelineRun row and return its id."""
    with session_scope() as session:
        run = PipelineRun(stream=stream, run_date=run_date, status="running")
        session.add(run)
        session.flush()
        run_id = run.id
    log.info("pipeline run %d started for %s on %s", run_id, stream, run_date)
    return run_id


def finish_run(run_id: int, cost_usd: float, *, status: str = "success") -> None:
    """Close a PipelineRun row."""
    with session_scope() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            log.warning("pipeline run %d not found; cannot close it", run_id)
            return
        run.status = status
        run.cost_usd = cost_usd
        run.finished_at = utcnow()
    log.info("pipeline run %d finished: %s ($%.4f)", run_id, status, cost_usd)
