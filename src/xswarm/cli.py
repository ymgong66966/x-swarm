from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import costs
from .agents import (
    analyst,
    composer,
    curator,
    editor,
    measurer,
    publisher,
    scout,
    strategist,
    visualizer,
    writer,
)
from .analytics import crawl, dashboard, traffic
from .care import export as care_export
from .care import site as care_site
from .care.graph import run_pipeline as run_care_pipeline
from .config import settings
from .db import init_db, session_scope
from .evals import harness
from .graph import run_pipeline
from .llm import LLM
from .models import (
    Article,
    Asset,
    Brief,
    Candidate,
    Draft,
    Item,
    ModelCall,
    PostMetric,
    Publication,
    SiteFact,
)

app = typer.Typer(help="Agent swarm for an ML-frontier X account", no_args_is_help=True)
care_app = typer.Typer(help="Care stream: healthcare articles and their promo posts")
app.add_typer(care_app, name="care")
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
        composer.run(session, llm, drafts)
        editor.run(session, llm, drafts)
        costs.record(session, llm)
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
        f"threads={len(state.get('thread_ids', []))} "
        f"ready={len(state.get('ready_ids', []))} "
        f"visuals={len(state.get('asset_ids', []))} "
        f"cost=${state.get('cost_usd', 0.0):.3f}"
    )
    with session_scope() as session:
        _print_drafts([session.get(Draft, did) for did in state.get("draft_ids", [])])


@app.command("roundup")
def roundup_cmd(dry_run: bool = False, verbose: bool = False) -> None:
    """Compose the weekly curation thread from the last 7 days of candidates."""
    _setup_logging(verbose)
    init_db()
    llm = LLM(dry_run=dry_run)
    with session_scope() as session:
        draft = composer.weekly_roundup(session, llm)
        if draft is None:
            console.print("[yellow]not enough candidates this week[/yellow]")
            return
        editor.run(session, llm, [draft])
        costs.record(session, llm)
        console.print(f"draft {draft.id} [{draft.status}]")
        for index, post in enumerate([draft.body, *draft.thread]):
            console.print(f"[bold]{index + 1}.[/bold] {post}")
        if draft.editor_notes:
            console.print(f"[red]{'; '.join(draft.editor_notes)}[/red]")


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


@app.command("render")
def render_cmd(draft_id: list[int] = typer.Option(None), dry_run: bool = False) -> None:
    """Render (or re-render) the visual for specific drafts."""
    init_db()
    with session_scope() as session:
        drafts = [session.get(Draft, did) for did in draft_id or []]
        assets = visualizer.run(session, LLM(dry_run=dry_run), [d for d in drafts if d])
        for asset in assets:
            console.print(f"{asset.kind}: {asset.path}")


@app.command("publish")
def publish_cmd(
    dry_run: bool = False,
    schedule_only: bool = typer.Option(
        True,
        help="Queue the draft in Typefully without auto-publishing (phase-1 autonomy).",
    ),
    limit: int = typer.Option(None, help="Cap how many drafts are scheduled"),
    verbose: bool = False,
) -> None:
    """Send approved drafts to Typefully at the next free posting slots."""
    _setup_logging(verbose)
    init_db()
    if not settings.typefully_api_key and not dry_run:
        console.print("[yellow]XSWARM_TYPEFULLY_API_KEY unset — dry run[/yellow]")
    with session_scope() as session:
        publications = publisher.run(
            session, dry_run=dry_run, plan_only=schedule_only, limit=limit
        )
        table = Table("draft", "status", "scheduled_for", "provider id")
        for publication in publications:
            table.add_row(
                str(publication.draft_id),
                publication.status,
                publication.scheduled_for.isoformat() if publication.scheduled_for else "",
                publication.provider_draft_id or "",
            )
        console.print(table)


@app.command("sync-metrics")
def sync_metrics_cmd(days: int = typer.Option(None), verbose: bool = False) -> None:
    """Pull X analytics for published posts from Typefully."""
    _setup_logging(verbose)
    init_db()
    if not settings.typefully_api_key:
        raise typer.BadParameter("XSWARM_TYPEFULLY_API_KEY is required to sync metrics")
    with session_scope() as session:
        stored = measurer.run(session, days=days)
        console.print(f"[green]{stored} snapshots stored[/green]")


@app.command("strategy")
def strategy_cmd(
    days: int = 28,
    dry_run: bool = False,
    write: bool = typer.Option(True, help="Rewrite playbook.md with the new strategy"),
    verbose: bool = False,
) -> None:
    """Aggregate performance and rewrite the playbook the Writer follows."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        console.print(strategist.report(strategist.aggregate(session, days=days)))
        console.print(
            strategist.run(session, LLM(dry_run=dry_run), days=days, write_playbook=write)
        )


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
    table = Table("id", "status", "hook", "posts", "body", "notes")
    for draft in drafts:
        if draft is None:
            continue
        table.add_row(
            str(draft.id),
            draft.status,
            str(draft.features.get("hook_style", "")),
            str(1 + len(draft.thread or [])),
            draft.body[:120],
            "; ".join(draft.editor_notes)[:60],
        )
    console.print(table)


@app.command("cost")
def cost_cmd(days: int = 30) -> None:
    """Model spend per agent, and whether this month is on track for the budget."""
    init_db()
    with session_scope() as session:
        table = Table("agent", "calls", "prompt tok", "completion tok", "usd")
        for agent, calls, prompt, completion, usd in costs.by_agent(session, days=days):
            table.add_row(agent, str(calls), f"{prompt:,}", f"{completion:,}", f"${usd:.3f}")
        console.print(table)
        mtd = costs.month_to_date(session)
        projected = costs.projected_month(session)
        colour = "red" if costs.over_budget(session) else "green"
        console.print(
            f"month to date ${mtd:.2f} | projected [{colour}]${projected:.2f}[/{colour}] "
            f"of ${settings.monthly_budget_usd:.2f} budget"
        )


@app.command("eval")
def eval_cmd(
    dry_run: bool = True,
    min_score: float = typer.Option(0.0, help="Exit non-zero below this overall score"),
    json_out: str = typer.Option("", help="Write the full report as JSON here"),
) -> None:
    """Score the Writer and Editor against frozen briefs. No network unless --no-dry-run."""
    report = harness.run_eval(dry_run=dry_run)
    console.print(harness.format_report(report))
    if json_out:
        Path(json_out).write_text(json.dumps(report.to_dict(), indent=2))
        console.print(f"wrote {json_out}")
    if report.overall < min_score:
        console.print(f"[red]overall {report.overall} below {min_score}[/red]")
        raise typer.Exit(1)


@app.command("stats")
def stats_cmd() -> None:
    """Row counts, for sanity-checking a run."""
    init_db()
    with session_scope() as session:
        for model in (
            Item,
            Candidate,
            Brief,
            Draft,
            Asset,
            Publication,
            PostMetric,
            ModelCall,
            SiteFact,
            Article,
        ):
            console.print(f"{model.__tablename__}: {session.query(model).count()}")


@care_app.command("sync-site")
def care_sync_site_cmd(verbose: bool = False) -> None:
    """Re-read alvernahealth.com so product claims come from the live site."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        facts = care_site.sync(session)
        table = Table("audience", "section", "fact")
        for fact in facts[:30]:
            table.add_row(fact.audience, fact.section[:30], fact.text[:90])
        console.print(table)
        console.print(f"[green]{len(facts)} new facts[/green]")


@care_app.command("run")
def care_run_cmd(
    dry_run: bool = False,
    sync_site: bool = typer.Option(True, help="Refresh product facts from the website first"),
    verbose: bool = False,
) -> None:
    """Full care pipeline: research -> curate -> plan -> write -> compliance -> promos."""
    _setup_logging(verbose)
    state = run_care_pipeline(dry_run=dry_run, sync_site=sync_site)
    console.print(
        f"items={len(state.get('item_ids', []))} "
        f"candidates={len(state.get('candidate_ids', []))} "
        f"articles={len(state.get('article_ids', []))} "
        f"ready={len(state.get('ready_article_ids', []))} "
        f"promos={len(state.get('promo_ids', []))} "
        f"cost=${state.get('cost_usd', 0.0):.3f}"
    )
    with session_scope() as session:
        table = Table("id", "status", "audience", "words", "title", "blocked because")
        for article_id in state.get("article_ids", []):
            article = session.get(Article, article_id)
            if article is None:
                continue
            table.add_row(
                str(article.id),
                article.status,
                article.audience,
                str(article.word_count),
                article.title[:60],
                "; ".join(article.editor_notes)[:70],
            )
        console.print(table)
        _print_drafts([session.get(Draft, did) for did in state.get("promo_ids", [])])


@care_app.command("articles")
def care_articles_cmd(limit: int = 10, status: str = "") -> None:
    """List recent articles and where their markdown lives."""
    init_db()
    with session_scope() as session:
        query = session.query(Article).order_by(Article.id.desc())
        if status:
            query = query.filter(Article.status == status)
        table = Table("id", "status", "audience", "words", "sources", "title")
        for article in query.limit(limit):
            table.add_row(
                str(article.id),
                article.status,
                article.audience,
                str(article.word_count),
                str(len(article.sources)),
                article.title[:70],
            )
        console.print(table)
        console.print(f"markdown in {settings.care_articles_dir}")


@care_app.command("export")
def care_export_cmd(article_id: list[int] = typer.Option(None)) -> None:
    """Write articles to markdown files (all review-ready ones by default)."""
    init_db()
    with session_scope() as session:
        if article_id:
            articles = [session.get(Article, aid) for aid in article_id]
        else:
            articles = list(session.query(Article).filter(Article.status == "ready_for_review"))
        for path in care_export.run([a for a in articles if a is not None]):
            console.print(str(path))


@app.command("crawl")
def crawl_cmd(url: list[str] = typer.Option(None), verbose: bool = False) -> None:
    """Check robots, sitemap, indexability and metadata for the site's pages."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        table = Table("url", "http", "robots", "sitemap", "indexable", "issues")
        for check in crawl.run(session, list(url) if url else None):
            table.add_row(
                check.url[:60],
                str(check.status_code),
                "ok" if check.robots_allowed else "BLOCKED",
                "yes" if check.in_sitemap else "no",
                "yes" if check.indexable else "NO",
                "; ".join(check.issues)[:60],
            )
        console.print(table)


@app.command("sync-traffic")
def sync_traffic_cmd(days: int = typer.Option(None), verbose: bool = False) -> None:
    """Pull page traffic for published articles from Plausible."""
    _setup_logging(verbose)
    init_db()
    if not traffic.configured():
        raise typer.BadParameter(
            "XSWARM_PLAUSIBLE_API_KEY and XSWARM_PLAUSIBLE_SITE_ID are required"
        )
    with session_scope() as session:
        snapshots = traffic.collect(session, days=days)
        table = Table("url", "visitors", "views", "bounce", "avg s")
        for snapshot in snapshots:
            table.add_row(
                snapshot.url[:60],
                str(snapshot.visitors),
                str(snapshot.pageviews),
                f"{snapshot.bounce_rate:.0f}%",
                f"{snapshot.avg_seconds:.0f}",
            )
        console.print(table)


@app.command("dashboard")
def dashboard_cmd(
    days: int = typer.Option(None, help="Lookback window"),
    json_out: str = typer.Option("", help="Also write the raw numbers as JSON here"),
) -> None:
    """Build the both-streams dashboard as a single HTML file."""
    init_db()
    with session_scope() as session:
        report = dashboard.build(session, days=days)
        table = Table("stream", "pieces", "ready", "published", "impressions", "eng rate", "cost")
        for summary in report.streams:
            table.add_row(
                summary.stream,
                str(summary.drafted),
                f"{summary.ready} ({summary.pass_rate:.0%})",
                str(summary.published),
                f"{summary.impressions:,}",
                f"{summary.engagement_rate:.2%}",
                f"${summary.cost_usd:.2f}",
            )
        console.print(table)
        path = dashboard.write(session, days=days)
        console.print(f"[green]{path}[/green]")
        if json_out:
            Path(json_out).write_text(json.dumps(report.as_dict(), indent=2))
            console.print(f"wrote {json_out}")


if __name__ == "__main__":
    app()
