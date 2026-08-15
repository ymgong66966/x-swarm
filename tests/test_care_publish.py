"""Publishing an approved article to the site repo as a pull request."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from xswarm.care import publish
from xswarm.config import settings
from xswarm.models import Article

TODAY = dt.date(2026, 8, 14)


def make_article(**kwargs) -> Article:
    body = (
        "Providers keep leaving caregiver training unbilled.\n\n"
        "## What changed\n\nCMS finalised codes 97550-97552.\n\n"
        "## Key takeaways\n\n- Document the caregiver's role.\n\n"
        "## Frequently asked questions\n\n"
        "**Can this be furnished over telehealth?**\n\n"
        "Yes, while the codes remain on the telehealth list.\n\n"
        "**Who has to be present?**\n\n"
        "The caregiver; the patient does not.\n\n"
        f"_{settings.care_disclaimer}_\n\n"
        "## Sources\n\n- [CY2025 PFS](https://www.cms.gov/pfs) — regulatory\n"
    )
    defaults = dict(
        id=7,
        run_date=TODAY,
        pillar="reimbursement_mechanics",
        audience="provider",
        thesis="Caregiver training is billable and most providers are not billing it.",
        title="Caregiver training you are allowed to bill for",
        slug="caregiver-training-you-can-bill",
        dek="Three code families, one documentation habit.",
        meta_description="What CTS codes cover and how to document them.",
        keywords=["caregiver training", "CTS"],
        body_md=body,
        word_count=1200,
        sources=[{"url": "https://www.cms.gov/pfs", "title": "CY2025 PFS", "kind": "regulatory"}],
        evidence=[],
        status=publish.STATUS_APPROVED,
        editor_notes=[],
    )
    defaults.update(kwargs)
    return Article(**defaults)


def parse_frontmatter(markdown: str) -> dict[str, object]:
    block = markdown.split("---", 2)[1]
    data: dict[str, object] = {}
    for line in block.strip().splitlines():
        key, _, value = line.partition(":")
        data[key.strip()] = json.loads(value.strip())
    return data


def test_frontmatter_is_json_per_line_so_the_site_parser_agrees() -> None:
    data = parse_frontmatter(publish.render(make_article()))
    assert data["slug"] == "caregiver-training-you-can-bill"
    assert data["date"] == "2026-08-14"
    assert data["audience"] == "provider"
    assert data["sources"] == [
        {"url": "https://www.cms.gov/pfs", "title": "CY2025 PFS", "kind": "regulatory"}
    ]


def test_faq_moves_into_frontmatter_and_out_of_the_body() -> None:
    markdown = publish.render(make_article())
    data = parse_frontmatter(markdown)
    faq = data["faq"]
    assert isinstance(faq, list) and len(faq) == 2
    assert faq[0]["q"] == "Can this be furnished over telehealth?"
    assert faq[0]["a"].startswith("Yes, while the codes")
    body = markdown.split("---", 2)[2]
    assert "Frequently asked questions" not in body


def test_body_drops_the_blocks_the_site_template_renders_itself() -> None:
    body = publish.render(make_article()).split("---", 2)[2]
    assert settings.care_disclaimer not in body
    assert "## Sources" not in body
    assert "## What changed" in body  # the article itself survives


def test_hero_is_referenced_by_its_served_url_not_its_repo_path(tmp_path: Path) -> None:
    hero = tmp_path / "art.png"
    hero.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = publish.publish(make_article(), hero_path=hero, hero_alt="A chart", dry_run=True)
    data = parse_frontmatter(result.markdown)
    assert result.media_path == "public/resources/media/caregiver-training-you-can-bill.png"
    assert data["hero"] == "/resources/media/caregiver-training-you-can-bill.png"
    assert data["heroAlt"] == "A chart"


def test_unapproved_articles_are_refused() -> None:
    with pytest.raises(publish.PublishError, match="ready_for_review"):
        publish.publish(make_article(status="ready_for_review"), dry_run=True)


def test_a_hero_that_is_not_an_image_is_refused(tmp_path: Path) -> None:
    hero = tmp_path / "notes.txt"
    hero.write_text("not an image")
    with pytest.raises(publish.PublishError, match="not an image"):
        publish.publish(make_article(), hero_path=hero, dry_run=True)


def test_dry_run_touches_no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args, **kwargs):  # pragma: no cover - only runs if the gate breaks
        raise AssertionError("dry run must not shell out to git")

    monkeypatch.setattr(subprocess, "run", explode)
    result = publish.publish(make_article(), dry_run=True)
    assert result.dry_run and not result.pr_url


def test_publish_commits_on_a_branch_and_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Against a real local clone, so the git plumbing is exercised, not mocked."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True)
    (seed / "README.md").write_text("site\n")
    for args in (
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "test"],
        ["add", "README.md"],
        ["commit", "-m", "init"],
        ["push", "-u", "origin", "main"],
    ):
        subprocess.run(["git", *args], cwd=seed, check=True)

    monkeypatch.setattr(settings, "site_repo_url", str(origin))
    monkeypatch.setattr(settings, "github_token", None)
    checkout = tmp_path / "site"

    hero = tmp_path / "hero.png"
    hero.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = publish.publish(make_article(), hero_path=hero, repo_dir=checkout)

    assert result.branch.startswith("article/caregiver-training-you-can-bill-")
    assert result.compare_url.endswith(result.branch)
    assert not result.pr_url  # no token, so a human clicks the compare URL
    pushed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", result.branch],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert result.content_path in pushed
    assert result.media_path in pushed
    # main is untouched: merging the PR is the publication event.
    main_files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main"],
        cwd=origin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert main_files == ["README.md"]


def test_pull_edits_reads_the_reviewers_version_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A human edits the file in the PR; our row must follow, or the promos quote stale text."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True)
    (seed / "README.md").write_text("site\n")
    for args in (
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "test"],
        ["add", "README.md"],
        ["commit", "-m", "init"],
        ["push", "-u", "origin", "main"],
    ):
        subprocess.run(["git", *args], cwd=seed, check=True)

    monkeypatch.setattr(settings, "site_repo_url", str(origin))
    monkeypatch.setattr(settings, "github_token", None)
    article = make_article()
    result = publish.publish(article, repo_dir=tmp_path / "site")
    article.site_branch = result.branch

    # Stand in for the reviewer editing the file in GitHub's web editor.
    subprocess.run(["git", "fetch", "origin", result.branch], cwd=seed, check=True)
    subprocess.run(["git", "checkout", result.branch], cwd=seed, check=True)
    edited = (seed / result.content_path).read_text()
    edited = edited.replace(
        '"Caregiver training you are allowed to bill for"', '"Bill for caregiver training"'
    ).replace("CMS finalised codes 97550-97552.", "CMS finalized codes 97550-97552 in CY2025.")
    (seed / result.content_path).write_text(edited)
    subprocess.run(["git", "commit", "-am", "edit"], cwd=seed, check=True)
    subprocess.run(["git", "push"], cwd=seed, check=True)

    changed = publish.pull_edits(article, repo_dir=tmp_path / "site")

    assert set(changed) >= {"title", "body_md"}
    assert article.title == "Bill for caregiver training"
    assert "finalized codes 97550-97552 in CY2025" in article.body_md
    # The FAQ the site owns comes back into the body, so nothing the reviewer sees is lost.
    assert "Can this be furnished over telehealth?" in article.body_md


def test_pull_edits_falls_back_to_main_once_the_branch_is_merged_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub deletes the head branch on merge; `care watch` still has to find the text."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True)
    (seed / "README.md").write_text("site\n")
    for args in (
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "test"],
        ["add", "README.md"],
        ["commit", "-m", "init"],
        ["push", "-u", "origin", "main"],
    ):
        subprocess.run(["git", *args], cwd=seed, check=True)

    monkeypatch.setattr(settings, "site_repo_url", str(origin))
    monkeypatch.setattr(settings, "github_token", None)
    article = make_article()
    result = publish.publish(article, repo_dir=tmp_path / "site")
    article.site_branch = result.branch

    subprocess.run(["git", "fetch", "origin", result.branch], cwd=seed, check=True)
    subprocess.run(["git", "checkout", result.branch], cwd=seed, check=True)
    edited = (seed / result.content_path).read_text()
    (seed / result.content_path).write_text(
        edited.replace(
            '"Caregiver training you are allowed to bill for"', '"Bill for caregiver training"'
        )
    )
    subprocess.run(["git", "commit", "-am", "edit"], cwd=seed, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=seed, check=True)
    subprocess.run(["git", "merge", "--no-ff", "-m", "merge", result.branch], cwd=seed, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True)
    subprocess.run(["git", "push", "origin", "--delete", result.branch], cwd=seed, check=True)

    assert "title" in publish.pull_edits(article, repo_dir=tmp_path / "site")
    assert article.title == "Bill for caregiver training"


def test_pull_edits_without_a_publish_branch_is_refused() -> None:
    with pytest.raises(publish.PublishError, match="no publish branch"):
        publish.pull_edits(make_article(site_branch=None))


def test_apply_edits_is_idempotent_so_syncing_twice_reports_no_change() -> None:
    """The first sync normalises our body to the file's shape; a second must be a no-op."""
    article = make_article()
    publish.apply_edits(article, publish.render(article))
    assert publish.apply_edits(article, publish.render(article)) == []


def test_edited_frontmatter_that_is_not_json_fails_loudly() -> None:
    broken = "---\ntitle: Bill for caregiver training\n---\n\nBody.\n"
    with pytest.raises(publish.PublishError, match="is not JSON"):
        publish.apply_edits(make_article(), broken)


def test_pull_requests_open_as_drafts_so_the_reviewer_can_edit_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    def fake_post(url: str, **kwargs) -> httpx.Response:
        sent.update(kwargs["json"])
        return httpx.Response(201, json={"html_url": "https://github.com/x/y/pull/9"})

    monkeypatch.setattr(settings, "github_token", "t")
    monkeypatch.setattr(httpx, "post", fake_post)

    assert publish.open_pull_request("article/x", "Publish: x", "body") == (
        "https://github.com/x/y/pull/9"
    )
    assert sent["draft"] is True
    publish.open_pull_request("article/x", "Publish: x", "body", draft=False)
    assert sent["draft"] is False


def test_is_live_only_accepts_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if "missing" in request.url.path else 200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert publish.is_live("https://alvernahealth.com/resources/live", client) == (True, 200)
    assert publish.is_live("https://alvernahealth.com/resources/missing", client) == (False, 404)


def test_a_200_that_is_only_the_spa_shell_is_not_live() -> None:
    """The host answers 200 with the homepage for every unknown path, so 200 proves nothing."""
    shell = "<html><title>Alverna</title><link rel=canonical href=https://alvernahealth.com/>"
    article = "<html><link rel=canonical href=https://alvernahealth.com/resources/live>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=article if "live" in request.url.path else shell)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    marker = publish.article_marker(make_article(slug="ghost"))
    assert publish.is_live("https://alvernahealth.com/resources/ghost", client, marker=marker) == (
        False,
        200,
    )
    marker = publish.article_marker(make_article(slug="live"))
    assert publish.is_live("https://alvernahealth.com/resources/live", client, marker=marker) == (
        True,
        200,
    )


def test_a_reviewer_deleting_a_frontmatter_line_does_not_blank_the_field() -> None:
    article = make_article()
    publish.apply_edits(article, publish.render(article))  # settle the body into the file's shape
    without_dek = "\n".join(
        line for line in publish.render(article).splitlines() if not line.startswith("dek: ")
    )
    assert publish.apply_edits(article, without_dek) == []
    assert article.dek == "Three code families, one documentation habit."


def test_renaming_the_slug_in_the_pull_request_is_refused() -> None:
    """The URL, the promote gate and the promo links all come from our slug, not the file's."""
    article = make_article()
    renamed = publish.render(article).replace(
        '"caregiver-training-you-can-bill"', '"bill-for-caregiver-training"'
    )
    with pytest.raises(publish.PublishError, match="renames the slug"):
        publish.apply_edits(article, renamed)
    assert article.slug == "caregiver-training-you-can-bill"


def test_is_live_treats_an_unreachable_host_as_not_live() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert publish.is_live("https://alvernahealth.com/resources/x", client) == (False, 0)
