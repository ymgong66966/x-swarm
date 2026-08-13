from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents import analyst, curator, editor, scout, writer
from .db import init_db, session_scope
from .llm import LLM

log = logging.getLogger(__name__)


def _last(_left, right):
    return right


class PipelineState(TypedDict, total=False):
    run_date: Annotated[dt.date, _last]
    dry_run: Annotated[bool, _last]
    sources: Annotated[list[str] | None, _last]
    item_ids: Annotated[list[int], _last]
    candidate_ids: Annotated[list[int], _last]
    brief_ids: Annotated[list[int], _last]
    draft_ids: Annotated[list[int], _last]
    ready_ids: Annotated[list[int], _last]


def _llm(state: PipelineState) -> LLM:
    return LLM(dry_run=state.get("dry_run", False))


def scout_node(state: PipelineState) -> PipelineState:
    with session_scope() as session:
        items = scout.run(session, only=state.get("sources"))
        return {"item_ids": [i.id for i in items]}


def curator_node(state: PipelineState) -> PipelineState:
    with session_scope() as session:
        candidates = curator.run(session, _llm(state), run_date=state["run_date"])
        return {"candidate_ids": [c.id for c in candidates]}


def analyst_node(state: PipelineState) -> PipelineState:
    from .models import Candidate

    with session_scope() as session:
        candidates = [session.get(Candidate, cid) for cid in state["candidate_ids"]]
        briefs = analyst.run(session, _llm(state), [c for c in candidates if c])
        return {"brief_ids": [b.id for b in briefs]}


def writer_node(state: PipelineState) -> PipelineState:
    from .models import Brief

    with session_scope() as session:
        briefs = [session.get(Brief, bid) for bid in state["brief_ids"]]
        drafts = writer.run(session, _llm(state), [b for b in briefs if b])
        return {"draft_ids": [d.id for d in drafts]}


def editor_node(state: PipelineState) -> PipelineState:
    from .models import Draft

    with session_scope() as session:
        drafts = [session.get(Draft, did) for did in state["draft_ids"]]
        reviewed = editor.run(session, _llm(state), [d for d in drafts if d])
        return {"ready_ids": [d.id for d in reviewed if d.status == "ready_for_review"]}


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("scout", scout_node)
    graph.add_node("curator", curator_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("writer", writer_node)
    graph.add_node("editor", editor_node)

    graph.add_edge(START, "scout")
    graph.add_edge("scout", "curator")
    graph.add_edge("curator", "analyst")
    graph.add_edge("analyst", "writer")
    graph.add_edge("writer", "editor")
    graph.add_edge("editor", END)
    return graph.compile()


def run_pipeline(
    *,
    dry_run: bool = False,
    run_date: dt.date | None = None,
    sources: list[str] | None = None,
) -> PipelineState:
    init_db()
    initial: PipelineState = {
        "run_date": run_date or dt.date.today(),
        "dry_run": dry_run,
        "sources": sources,
    }
    return build_graph().invoke(initial)
