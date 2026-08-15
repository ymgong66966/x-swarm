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
    illustrator,
    measurer,
    publisher,
    scout,
    strategist,
    visualizer,
    writer,
)
from .analytics import crawl, dashboard, traffic
from .care import export as care_export
from .care import promoter as care_promoter
from .care import publish as care_publish
from .care import scorecard as care_scorecard
from .care import site as care_site
from .care.graph import run_pipeline as run_care_pipeline
from .config import settings
from .db import init_db, session_scope
from .evals import harness
from .graph import run_pipeline
from .ingest import fetch as ingest_fetch
from .ingest import pipeline as ingest_pipeline
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
ingest_app = typer.Typer(help="Your own material: a link, a paper, a post, or text")
app.add_typer(ingest_app, name="ingest")
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


@ingest_app.command("add")
def ingest_add_cmd(
    source: str = typer.Argument(
        ..., help="A URL, an arXiv id, a path to a file, or the text itself"
    ),
    image: list[Path] = typer.Option(
        None, help="Your own image(s) to attach instead of generating one"
    ),
    alt: str = typer.Option("", help="Alt text for images you supply"),
    illustrate: bool = typer.Option(True, help="Generate a house-style image"),
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Turn your own material into an X thread, illustrated and edited, for review."""
    _setup_logging(verbose)
    init_db()
    try:
        for path in image or []:
            ingest_pipeline.check_image(path)
        material = ingest_fetch.load(source)
    except ingest_fetch.IngestError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not material.text.strip():
        raise typer.BadParameter("nothing readable in that source")
    llm = LLM(dry_run=dry_run)
    with session_scope() as session:
        draft = ingest_pipeline.run(
            session,
            material,
            llm,
            images=list(image or []),
            alt=alt,
            illustrate_it=illustrate,
        )
        spend = costs.record(session, llm)
        console.print(f"[bold]{material.title}[/bold] ({material.kind})")
        _print_drafts([draft])
        console.print(f"draft {draft.id} is [bold]{draft.status}[/bold], ${spend:.3f}")


@ingest_app.command("schedule")
def ingest_schedule_cmd(
    draft_id: int,
    dry_run: bool = False,
    schedule_only: bool = typer.Option(True, help="Queue in Typefully without auto-publishing"),
) -> None:
    """Schedule one approved ingest draft. Approve it first with `xswarm approve`."""
    init_db()
    with session_scope() as session:
        draft = session.get(Draft, draft_id)
        if draft is None:
            raise typer.BadParameter(f"no draft {draft_id}")
        if draft.status != "approved":
            raise typer.BadParameter(
                f"draft {draft_id} is {draft.status}; approve it before scheduling"
            )
        publication = ingest_pipeline.schedule(
            session, draft, dry_run=dry_run, plan_only=schedule_only
        )
        when = publication.scheduled_for
        console.print(
            f"draft {draft_id} -> {publication.status} "
            f"at {when.isoformat() if when else 'unscheduled'}"
        )


@app.command("render")
def render_cmd(draft_id: list[int] = typer.Option(None), dry_run: bool = False) -> None:
    """Render (or re-render) the visual for specific drafts."""
    init_db()
    with session_scope() as session:
        drafts = [session.get(Draft, did) for did in draft_id or []]
        assets = visualizer.run(session, LLM(dry_run=dry_run), [d for d in drafts if d])
        for asset in assets:
            console.print(f"{asset.kind}: {asset.path}")


@app.command("illustrate")
def illustrate_cmd(
    draft_id: list[int] = typer.Option(None, help="Drafts to illustrate"),
    style: str = typer.Option("", help=f"Force one of: {', '.join(settings.art_styles)}"),
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Generate a house-style image for specific drafts, in any stream."""
    _setup_logging(verbose)
    init_db()
    if style and style not in settings.art_styles:
        raise typer.BadParameter(f"unknown style {style!r}")
    llm = LLM(dry_run=dry_run)
    with session_scope() as session:
        for did in draft_id or []:
            draft = session.get(Draft, did)
            if draft is None:
                raise typer.BadParameter(f"no draft {did}")
            if style:
                draft.features = {**(draft.features or {}), "art_style": style}
            asset = illustrator.illustrate(session, draft, llm)
            if asset is None:
                console.print(f"[yellow]draft {did}: no image provider[/yellow]")
                continue
            draft.features = {**(draft.features or {}), "visual_hint": asset.kind}
            console.print(f"draft {did}: {asset.spec['style']} -> {asset.path}")
        costs.record(session, llm)


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
        publications = publisher.run(session, dry_run=dry_run, plan_only=schedule_only, limit=limit)
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


@care_app.command("approve")
def care_approve_cmd(article_id: int, reject: bool = False, reason: str = "") -> None:
    """Clear an article for publication (or reject it). Publishing requires this."""
    init_db()
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise typer.BadParameter(f"no article {article_id}")
        if not reject and article.status not in {care_publish.STATUS_READY, "approved"}:
            raise typer.BadParameter(
                f"article {article_id} is '{article.status}'; only articles that cleared "
                "the compliance editor can be approved"
            )
        article.status = "rejected" if reject else care_publish.STATUS_APPROVED
        if reason:
            article.editor_notes = [*article.editor_notes, f"human: {reason}"]
        console.print(f"article {article_id} -> [bold]{article.status}[/bold]")


@care_app.command("publish")
def care_publish_cmd(
    article_id: int,
    hero: str = typer.Option("", help="Image file to ship as the article's hero"),
    hero_alt: str = typer.Option("", help="Alt text for the hero image"),
    illustrate: bool = typer.Option(
        True, help="Draw a hero for the article when one was not supplied"
    ),
    dry_run: bool = typer.Option(False, help="Render the file and stop; touch no git"),
    ready: bool = typer.Option(
        False, help="Open the PR ready for review instead of as a draft you still edit"
    ),
    verbose: bool = False,
) -> None:
    """Open a pull request on the site repo for an approved article. Merging publishes it."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise typer.BadParameter(f"no article {article_id}")
        hero_path = Path(hero) if hero else None
        if hero_path is None and illustrate and not dry_run:
            drawn = illustrator.illustrate_article(article, LLM())
            if drawn is None:
                console.print("[yellow]no hero image[/yellow] — publishing without one")
            else:
                hero_path, generated_alt = drawn
                hero_alt = hero_alt or generated_alt
                console.print(f"hero {hero_path}")
        try:
            result = care_publish.publish(
                article,
                hero_path=hero_path,
                hero_alt=hero_alt,
                dry_run=dry_run,
                draft=not ready,
            )
        except care_publish.PublishError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from None

        if dry_run:
            console.print(f"[yellow]dry run[/yellow] {result.content_path}")
            console.print(result.markdown[:1200])
            return
        article.site_pr_url = result.pr_url or result.compare_url
        article.site_branch = result.branch
        console.print(f"branch [bold]{result.branch}[/bold] -> {result.content_path}")
        console.print(result.pr_url or f"open the PR: {result.compare_url}")
        console.print(
            f"edit the markdown in the PR (press `.` on it), then "
            f"`xswarm care sync-edits {article_id}`"
        )
        console.print(f"lands at {result.article_url} once merged")


@care_app.command("sync-edits")
def care_sync_edits_cmd(article_id: int, verbose: bool = False) -> None:
    """Pull edits made in the site pull request back into the article."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise typer.BadParameter(f"no article {article_id}")
        try:
            changed = care_publish.pull_edits(article)
        except care_publish.PublishError as error:
            console.print(f"[red]{error}[/red]")
            raise typer.Exit(1) from None
        if not changed:
            console.print(f"article {article_id} already matches the pull request")
            return
        console.print(f"updated {', '.join(changed)} from {article.site_branch}")
        console.print(
            "[yellow]the promo posts still quote the pre-edit text[/yellow] — review them "
            f"(`xswarm review`) before `xswarm care promote {article_id}`"
        )


@care_app.command("status")
def care_status_cmd(check: bool = typer.Option(False, help="Also fetch each article URL")) -> None:
    """Where every article sits between draft and promoted."""
    init_db()
    table = Table(title="care articles")
    for column in ("id", "slug", "status", "site PR", "live", "promos"):
        table.add_column(column)
    with session_scope() as session:
        articles = session.query(Article).order_by(Article.id.desc()).limit(30).all()
        for article in articles:
            live = ""
            if check and article.site_branch:
                ok, code = care_publish.is_live(
                    care_publish.article_url(article),
                    marker=care_publish.article_marker(article),
                )
                live = "200" if ok else ("200, wrong page" if code == 200 else str(code) or "")
                live = live or "no response"
            approved = sum(1 for draft in article.promos if draft.status == "approved")
            table.add_row(
                str(article.id),
                article.slug,
                article.status,
                article.site_pr_url or "",
                live,
                f"{approved}/{len(article.promos)} approved",
            )
    console.print(table)


@care_app.command("scorecard")
def care_scorecard_cmd() -> None:
    """Per-article: findable by a crawler, and did X actually send anyone to the page.

    Search Console columns are missing on purpose until the property is connected — an
    empty column is information, an invented one is not.
    """
    init_db()
    table = Table(title="care article scorecard")
    columns = ("id", "slug", "status", "indexable", "sitemap", "promos", "impr", "clicks", "CTR")
    for column in columns:
        table.add_column(column)
    with session_scope() as session:
        for row in care_scorecard.build(session):
            flag = {True: "yes", False: "[red]no[/red]", None: "—"}
            table.add_row(
                str(row.article_id),
                row.slug,
                row.status,
                flag[row.indexable],
                flag[row.in_sitemap],
                f"{row.scheduled}/{row.promos} live",
                str(row.impressions),
                str(row.link_clicks),
                f"{row.click_rate:.1%}" if row.impressions else "—",
            )
    console.print(table)


@care_app.command("promote")
def care_promote_cmd(
    article_id: int,
    force: bool = typer.Option(
        False, help="Accept a bare 200 — use only if you have opened the URL yourself"
    ),
    verbose: bool = False,
) -> None:
    """Release an article's promo posts — only once its URL actually resolves."""
    _setup_logging(verbose)
    init_db()
    with session_scope() as session:
        article = session.get(Article, article_id)
        if article is None:
            raise typer.BadParameter(f"no article {article_id}")
        url = care_publish.article_url(article)
        marker = "" if force else care_publish.article_marker(article)
        live, status = care_publish.is_live(url, marker=marker)
        if not live:
            reason = (
                "answered 200 with the site's fallback shell, not the article"
                if status == 200
                else f"returned {status or 'no response'}"
            )
            console.print(
                f"[red]{url} {reason}[/red] — merge the site PR first and give the deploy a "
                "minute; promos must never link to a page that is not there"
            )
            if status == 200:
                console.print(
                    "if the page does look right in a browser, the host is not serving the "
                    f"prerendered head tags — `xswarm care promote {article_id} --force`"
                )
            raise typer.Exit(1)
        article.published_url = url
        article.status = "published"
        approved = care_promoter.release(session, article)
        console.print(f"{url} is live; approved {approved} promo posts for scheduling")


@care_app.command("watch")
def care_watch_cmd(verbose: bool = False) -> None:
    """Sync reviewer edits and promote every article whose site PR has already landed.

    The unattended half of the loop: run it on a schedule (or on a merge webhook) and the
    only human actions left are editing the PR and merging it.
    """
    _setup_logging(verbose)
    init_db()
    table = Table("id", "slug", "synced", "live", "promos")
    with session_scope() as session:
        pending = (
            session.query(Article)
            .filter(Article.site_branch.isnot(None), Article.status != "published")
            .order_by(Article.id)
            .all()
        )
        for article in pending:
            try:
                changed = care_publish.pull_edits(article)
            except care_publish.PublishError as error:
                table.add_row(str(article.id), article.slug, f"[red]{error}[/red]", "", "")
                continue
            url = care_publish.article_url(article)
            live, status = care_publish.is_live(url, marker=care_publish.article_marker(article))
            promoted = ""
            if live:
                article.published_url = url
                article.status = "published"
                promoted = f"approved {care_promoter.release(session, article)}"
            table.add_row(
                str(article.id),
                article.slug,
                ", ".join(changed) or "no change",
                "yes" if live else f"not yet ({status or 'no response'})",
                promoted,
            )
        if not pending:
            console.print("no article is waiting on a site pull request")
            return
    console.print(table)


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
