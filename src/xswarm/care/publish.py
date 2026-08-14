"""Publish an approved article to the company site as a pull request.

The site (alverna-health/alverna-site) reads `content/resources/*.md`, so publishing is:
render the article in that repo's front-matter dialect, commit it on a branch, push, and
open a PR. Merging the PR is the publication event, which keeps a human in the loop
without needing an approval system of our own — GitHub already is one.

Nothing here writes to `main`, and nothing publishes an article the reviewer has not
moved to `approved` first.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import settings
from ..models import Article

log = logging.getLogger(__name__)

STATUS_APPROVED = "approved"
STATUS_READY = "ready_for_review"

FAQ_HEADING = "## Frequently asked questions"
SOURCES_HEADING = "## Sources"
_FAQ_ENTRY = re.compile(r"^\*\*(?P<q>.+?)\*\*\s*\n+(?P<a>.+?)(?=\n\*\*|\Z)", re.DOTALL | re.M)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class PublishError(RuntimeError):
    """Raised for anything a human has to fix: bad state, bad asset, failed git."""


@dataclass
class PublishResult:
    slug: str
    branch: str
    content_path: str
    media_path: str
    markdown: str
    article_url: str
    pr_url: str = ""
    compare_url: str = ""
    dry_run: bool = False


# --------------------------------------------------------------------------- rendering


def split_body(body_md: str) -> tuple[str, list[dict[str, str]]]:
    """Return (body without the site-owned blocks, FAQ entries).

    Our markdown ends with FAQ, disclaimer and sources sections because the plain-file
    export has nowhere else to put them. The site page renders all three from front
    matter and its own template, so leaving them in the body would print each twice.
    """
    body = body_md
    faq: list[dict[str, str]] = []

    if FAQ_HEADING in body:
        body, _, tail = body.partition(FAQ_HEADING)
        after_faq = tail.split("\n## ", 1)
        block = after_faq[0]
        for match in _FAQ_ENTRY.finditer(block):
            answer = match.group("a").strip()
            # The disclaimer is emitted as a lone italic paragraph right after the FAQ.
            answer = answer.split("\n_", 1)[0].strip()
            if answer:
                faq.append({"q": match.group("q").strip(), "a": answer})
        body += "\n## " + after_faq[1] if len(after_faq) > 1 else ""

    if SOURCES_HEADING in body:
        body = body.split(SOURCES_HEADING, 1)[0]

    body = body.replace(f"_{settings.care_disclaimer}_", "")
    return re.sub(r"\n{3,}", "\n\n", body).strip(), faq


def frontmatter(article: Article, faq: list[dict[str, str]], hero: str, hero_alt: str) -> str:
    """One `key: <JSON value>` per line — the dialect `src/content/frontmatter.ts` parses.

    JSON rather than YAML so quoting, unicode and nested lists mean the same thing on
    both sides without either repo taking a YAML dependency.
    """
    fields: dict[str, object] = {
        "title": article.title,
        "slug": article.slug,
        "date": article.run_date.isoformat(),
        "description": article.meta_description,
        "audience": article.audience,
        "pillar": article.pillar,
        "dek": article.dek,
        "keywords": article.keywords,
        "sources": article.sources,
        "faq": faq,
    }
    if hero:
        fields["hero"] = hero
        fields["heroAlt"] = hero_alt or article.title
    lines = ["---"]
    lines += [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()]
    lines.append("---")
    return "\n".join(lines)


def render(article: Article, hero: str = "", hero_alt: str = "") -> str:
    body, faq = split_body(article.body_md)
    return f"{frontmatter(article, faq, hero, hero_alt)}\n\n{body}\n"


def article_url(article: Article) -> str:
    return f"{settings.care_blog_base_url.rstrip('/')}/{article.slug}"


def is_live(url: str, client: httpx.Client | None = None) -> tuple[bool, int]:
    """Whether the article actually resolves. Promo posts must not link to a 404."""
    close = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        status = client.get(url).status_code
    except httpx.HTTPError as error:
        log.warning("could not reach %s: %s", url, error)
        return False, 0
    finally:
        if close:
            client.close()
    return status == 200, status


# ---------------------------------------------------------------------------- git side


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise PublishError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    """Commit, falling back to a bot identity on machines with no git identity set."""
    identity = subprocess.run(
        ["git", "config", "user.email"], cwd=repo, capture_output=True, text=True, check=False
    )
    if identity.stdout.strip():
        _git(repo, "commit", "-m", message)
        return
    _git(
        repo,
        "-c",
        "user.name=x-swarm",
        "-c",
        "user.email=x-swarm@users.noreply.github.com",
        "commit",
        "-m",
        message,
    )


def ensure_checkout(repo_dir: Path | None = None) -> Path:
    """A clone of the site repo, on an up-to-date default branch."""
    repo = Path(repo_dir or settings.site_repo_dir)
    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", settings.site_repo_url, str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise PublishError(f"could not clone {settings.site_repo_url}: {result.stderr.strip()}")
    _git(repo, "fetch", "origin", settings.site_default_branch)
    _git(repo, "checkout", settings.site_default_branch)
    _git(repo, "reset", "--hard", f"origin/{settings.site_default_branch}")
    return repo


def _hero_target(hero_path: Path, slug: str) -> str:
    suffix = hero_path.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        raise PublishError(f"{hero_path} is not an image the site can serve ({suffix or 'no'} ext)")
    if not hero_path.is_file():
        raise PublishError(f"hero image {hero_path} does not exist")
    return f"{settings.site_media_dir}/{slug}{suffix}"


def open_pull_request(branch: str, title: str, body: str) -> str:
    """Open the PR through the API when a token is configured; otherwise return ""."""
    if not settings.github_token:
        return ""
    response = httpx.post(
        f"{settings.github_api_url.rstrip('/')}/repos/{settings.site_repo}/pulls",
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": title,
            "head": branch,
            "base": settings.site_default_branch,
            "body": body,
        },
        timeout=30.0,
    )
    if response.status_code >= 300:
        raise PublishError(
            f"GitHub refused the pull request ({response.status_code}): {response.text[:300]}"
        )
    return str(response.json().get("html_url", ""))


def _pr_body(article: Article, url: str) -> str:
    sources = "\n".join(
        f"- [{source.get('title') or source.get('url')}]({source.get('url')})"
        for source in article.sources[:10]
    )
    return (
        f"Generated by x-swarm's care stream and approved for publication.\n\n"
        f"**Audience:** {article.audience} · **Pillar:** {article.pillar} · "
        f"**Words:** {article.word_count}\n\n"
        f"**Thesis.** {article.thesis}\n\n"
        f"Lands at `{url}` once merged.\n\n"
        f"### Sources\n{sources or '- (none recorded)'}\n\n"
        f"Review the claims before merging; merging is what publishes it."
    )


def publish(
    article: Article,
    *,
    hero_path: Path | None = None,
    hero_alt: str = "",
    dry_run: bool = False,
    repo_dir: Path | None = None,
) -> PublishResult:
    """Approved article -> branch + PR on the site repo. Never touches `main`."""
    if article.status != STATUS_APPROVED:
        raise PublishError(
            f"article {article.id} is '{article.status}'; approve it first "
            f"(`xswarm care approve {article.id}`) — only approved articles are published"
        )

    slug = article.slug
    hero_target = _hero_target(hero_path, slug) if hero_path else ""
    hero_url = f"/{hero_target.removeprefix('public/')}" if hero_target else ""
    markdown = render(article, hero_url, hero_alt)
    content_path = f"{settings.site_content_dir}/{article.run_date.isoformat()}-{slug}.md"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M")
    branch = f"{settings.site_branch_prefix}/{slug}-{stamp}"
    url = article_url(article)

    result = PublishResult(
        slug=slug,
        branch=branch,
        content_path=content_path,
        media_path=hero_target,
        markdown=markdown,
        article_url=url,
        dry_run=dry_run,
    )
    if dry_run:
        log.info("dry run: would open %s with %s", branch, content_path)
        return result

    repo = ensure_checkout(repo_dir)
    _git(repo, "checkout", "-b", branch)
    target = repo / content_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown)
    paths = [content_path]
    if hero_path and hero_target:
        media = repo / hero_target
        media.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hero_path, media)
        paths.append(hero_target)

    _git(repo, "add", *paths)
    _commit(repo, f"Publish article: {article.title}")
    _git(repo, "push", "-u", "origin", branch)
    _git(repo, "checkout", settings.site_default_branch)

    result.compare_url = f"https://github.com/{settings.site_repo}/pull/new/{branch}"
    result.pr_url = open_pull_request(branch, f"Publish: {article.title}", _pr_body(article, url))
    log.info("pushed %s (%s)", branch, result.pr_url or result.compare_url)
    return result
