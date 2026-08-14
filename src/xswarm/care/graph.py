from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import costs
from ..config import settings
from ..db import init_db, session_scope
from ..llm import LLM
from ..models import Article, Candidate
from . import angle, curator, editor, export, promoter, scout, site, writer

log = logging.getLogger(__name__)


def _last(_left, right):
    return right


class CareState(TypedDict, total=False):
    run_date: Annotated[dt.date, _last]
    dry_run: Annotated[bool, _last]
    sources: Annotated[list[str] | None, _last]
    sync_site: Annotated[bool, _last]
    item_ids: Annotated[list[int], _last]
    candidate_ids: Annotated[list[int], _last]
    article_ids: Annotated[list[int], _last]
    ready_article_ids: Annotated[list[int], _last]
    promo_ids: Annotated[list[int], _last]
    cost_usd: Annotated[float, _last]


def _llm(state: CareState) -> LLM:
    return LLM(dry_run=state.get("dry_run", False))


def _spend(session, llm: LLM, state: CareState) -> float:
    return state.get("cost_usd", 0.0) + costs.record(session, llm, run_date=state["run_date"])


def site_node(state: CareState) -> CareState:
    if not state.get("sync_site", True):
        return {}
    with session_scope() as session:
        site.sync(session)
    return {}


def scout_node(state: CareState) -> CareState:
    with session_scope() as session:
        items = scout.run(session, only=state.get("sources"))
        return {"item_ids": [item.id for item in items]}


def curator_node(state: CareState) -> CareState:
    with session_scope() as session:
        candidates = curator.run(session, run_date=state["run_date"])
        return {"candidate_ids": [c.id for c in candidates]}


def writer_node(state: CareState) -> CareState:
    """Plan then write, per lead. Kept in one node because a plan is only meaningful
    together with the article it produced."""
    with session_scope() as session:
        llm = _llm(state)
        pool = [session.get(Candidate, cid) for cid in state.get("candidate_ids", [])]
        leads = [c for c in pool if c is not None]
        articles: list[Article] = []
        for lead in leads[: settings.care_articles_per_run]:
            plan = angle.plan(session, lead, leads, llm)
            articles.append(writer.write(session, plan, llm, state["run_date"]))
        return {
            "article_ids": [a.id for a in articles],
            "cost_usd": _spend(session, llm, state),
        }


def editor_node(state: CareState) -> CareState:
    with session_scope() as session:
        articles = [session.get(Article, aid) for aid in state.get("article_ids", [])]
        passed = editor.review(session, [a for a in articles if a is not None])
        if not state.get("dry_run", False):
            export.run(passed)
        return {"ready_article_ids": [a.id for a in passed]}


def promoter_node(state: CareState) -> CareState:
    with session_scope() as session:
        llm = _llm(state)
        articles = [session.get(Article, aid) for aid in state.get("ready_article_ids", [])]
        drafts = promoter.run(session, llm, [a for a in articles if a is not None])
        return {"promo_ids": [d.id for d in drafts], "cost_usd": _spend(session, llm, state)}


def build_graph():
    graph = StateGraph(CareState)
    graph.add_node("site", site_node)
    graph.add_node("scout", scout_node)
    graph.add_node("curator", curator_node)
    graph.add_node("writer", writer_node)
    graph.add_node("editor", editor_node)
    graph.add_node("promoter", promoter_node)

    graph.add_edge(START, "site")
    graph.add_edge("site", "scout")
    graph.add_edge("scout", "curator")
    graph.add_edge("curator", "writer")
    graph.add_edge("writer", "editor")
    # Only articles that cleared the gate get promoted; nothing links to a blocked piece.
    graph.add_edge("editor", "promoter")
    graph.add_edge("promoter", END)
    return graph.compile()


def run_pipeline(
    *,
    dry_run: bool = False,
    run_date: dt.date | None = None,
    sources: list[str] | None = None,
    sync_site: bool = True,
) -> CareState:
    init_db()
    initial: CareState = {
        "run_date": run_date or dt.date.today(),
        "dry_run": dry_run,
        "sources": sources,
        "sync_site": sync_site,
        "cost_usd": 0.0,
    }
    return build_graph().invoke(initial)
