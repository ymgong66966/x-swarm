"""Write cleared articles to disk as portable markdown.

The company site is not ours to deploy to, so the stream's output artefact is a folder
of front-mattered markdown that any static site, CMS import, or human can consume.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import settings
from ..models import Article

log = logging.getLogger(__name__)


def frontmatter(article: Article) -> str:
    fields = {
        "title": article.title,
        "slug": article.slug,
        "date": article.run_date.isoformat(),
        "audience": article.audience,
        "pillar": article.pillar,
        "description": article.meta_description,
        "keywords": article.keywords,
        "thesis": article.thesis,
        "sources": [source.get("url", "") for source in article.sources],
    }
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {json.dumps(value)}")
    lines.append("---")
    return "\n".join(lines)


def write_file(article: Article, directory: Path | None = None) -> Path:
    directory = directory or settings.care_articles_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{article.run_date.isoformat()}-{article.slug}.md"
    dek = f"\n_{article.dek}_\n" if article.dek else ""
    path.write_text(f"{frontmatter(article)}\n\n# {article.title}\n{dek}\n{article.body_md}")
    return path


def run(articles: list[Article], directory: Path | None = None) -> list[Path]:
    paths = [write_file(article, directory) for article in articles]
    log.info("exported %d articles", len(paths))
    return paths
