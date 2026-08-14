"""Ingest stream: your own links, papers, posts and notes turned into X threads."""

from . import fetch, pipeline
from .fetch import Material, load

__all__ = ["Material", "fetch", "load", "pipeline"]
