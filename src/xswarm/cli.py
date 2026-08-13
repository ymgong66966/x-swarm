from __future__ import annotations

import datetime as dt
import logging

import typer
from rich.console import Console
from rich.table import Table

from .agents import analyst, curator, editor, scout, writer
from .db import init_db, session_scope
from .graph import run_pipeline
from .llm import LLM
from .models import Brief, Candidate, Draft, Item

app = typer.Typer(help="Agent swarm for an ML-frontier X account", no_args_is_help=True)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )


@app.command("init")
def init() -> None:
    """Create the database schema."""
    init_db()
    console.print("[green]schema created[/green]")


@app.command("scout")
def scout_cmd(
    source: list[str] = typer.Option(None, help="Limit to specific sources"),
    verbose: bool = False,
) -> None:
    """Ingest today's items from all sources."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        new_items = scout.run(session, only=list(source) if source else None)
        table = Table("source", "title", "signals")
        for item in new_items[:40]:
            table.add_row(item.source, item.title[:80], str(item.signals)[:60])
        console.print(table)
        console.print(f"[green]{len(new_items)} new items[/green]")


@app.command("curate")
def curate_cmd(dry_run: bool = False, verbose: bool = False) -> None:
    """Score ingested items and shortlist today's candidates."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        candidates = curator.run(session, LLM(dry_run=dry_run))
        _print_candidates(candidates)


@app.command("draft")
def draft_cmd(dry_run: bool = False, verbose: bool = False) -> None:
    """Analyse today's candidates, write variants, and run the editor gate."""
    _setup_logging(verbose)
    init_db()
    llm = LLM(dry_run=dry_run)
    with session_scope() as session:
        candidates = list(
            session.query(Candidate)
            .filter(Candidate.run_date == dt.date.today())
            .order_by(Candidate.score.desc())
        )
        briefs = analyst.run(session, llm, candidates)
        drafts = writer.run(session, llm, briefs)
        editor.run(session, llm, drafts)
        _print_drafts(drafts)


@app.command("run")
def run_cmd(dry_run: bool = False, verbose: bool = False) -> None:
    """Run the full daily pipeline: scout -> curate -> analyse -> write -> edit."""
    _setup_logging(verbose)
    state = run_pipeline(dry_run=dry_run)
    console.print(
        f"items={len(state.get('item_ids', []))} "
        f"candidates={len(state.get('candidate_ids', []))} "
        f"briefs={len(state.get('brief_ids', []))} "
        f"drafts={len(state.get('draft_ids', []))} "
        f"ready={len(state.get('ready_ids', []))}"
    )
    with session_scope() as session:
        _print_drafts([session.get(Draft, did) for did in state.get("draft_ids", [])])


@app.command("review")
def review_cmd(status: str = "ready_for_review", limit: int = 20) -> None:
    """Show drafts waiting for your approval."""
    init_db()
    with session_scope() as session:
        drafts = (
            session.query(Draft)
            .filter(Draft.status == status)
            .order_by(Draft.created_at.desc())
            .limit(limit)
            .all()
        )
        _print_drafts(drafts)


@app.command("approve")
def approve_cmd(draft_id: int, reject: bool = False, reason: str = "") -> None:
    """Approve or reject a draft. Rejection reasons are the Writer's training signal."""
    init_db()
    with session_scope() as session:
        draft = session.get(Draft, draft_id)
        if draft is None:
            raise typer.BadParameter(f"no draft {draft_id}")
        draft.status = "rejected" if reject else "approved"
        if reason:
            draft.editor_notes = [*draft.editor_notes, f"human: {reason}"]
        console.print(f"draft {draft_id} -> [bold]{draft.status}[/bold]")


def _print_candidates(candidates: list[Candidate]) -> None:
    table = Table("id", "score", "pillar", "title", "subscores")
    for candidate in candidates:
        table.add_row(
            str(candidate.id),
            f"{candidate.score:.3f}",
            candidate.pillar,
            candidate.item.title[:70],
            ", ".join(f"{k[:3]}={v:.2f}" for k, v in candidate.subscores.items()),
        )
    console.print(table)


def _print_drafts(drafts: list[Draft | None]) -> None:
    table = Table("id", "status", "hook", "body", "notes")
    for draft in drafts:
        if draft is None:
            continue
        table.add_row(
            str(draft.id),
            draft.status,
            str(draft.features.get("hook_style", "")),
            draft.body[:120],
            "; ".join(draft.editor_notes)[:60],
        )
    console.print(table)


@app.command("stats")
def stats_cmd() -> None:
    """Row counts, for sanity-checking a run."""
    init_db()
    with session_scope() as session:
        for model in (Item, Candidate, Brief, Draft):
            console.print(f"{model.__tablename__}: {session.query(model).count()}")


if __name__ == "__main__":
    app()
