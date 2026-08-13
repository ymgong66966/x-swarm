from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


class Item(Base):
    """A raw thing the Scout found: a paper, release, or blog post."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_items_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list[str]] = mapped_column(default=list)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    signals: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidate: Mapped[Candidate | None] = relationship(back_populates="item", uselist=False)


class Candidate(Base):
    """An Item the Curator scored and shortlisted."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    run_date: Mapped[dt.date] = mapped_column(index=True)
    score: Mapped[float] = mapped_column(Float)
    subscores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    pillar: Mapped[str] = mapped_column(String(32), default="paper_of_the_day")
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(back_populates="candidate")
    brief: Mapped[Brief | None] = relationship(back_populates="candidate", uselist=False)


class Brief(Base):
    """The Analyst's grounded structured read of a candidate."""

    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    whats_new: Mapped[str] = mapped_column(Text, default="")
    what_it_replaces: Mapped[str] = mapped_column(Text, default="")
    key_number: Mapped[str] = mapped_column(Text, default="")
    caveat: Mapped[str] = mapped_column(Text, default="")
    builder_takeaway: Mapped[str] = mapped_column(Text, default="")
    grounded_claims: Mapped[list[str]] = mapped_column(default=list)
    unverified_claims: Mapped[list[str]] = mapped_column(default=list)
    visual_hint: Mapped[str] = mapped_column(String(32), default="concept_diagram")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="brief")
    drafts: Mapped[list[Draft]] = relationship(back_populates="brief")


class Draft(Base):
    """One Writer variant, plus the Editor's verdict.

    `brief_id` is nullable because a weekly roundup is about the week, not about one
    item; those drafts carry their grounding in `features["grounding"]` instead.
    """

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    brief_id: Mapped[int | None] = mapped_column(ForeignKey("briefs.id"), index=True)
    variant: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text)
    # Posts 2..n of a thread. The link reply is always appended after these.
    thread: Mapped[list[str]] = mapped_column(default=list)
    link_reply: Mapped[str] = mapped_column(Text, default="")
    alt_text: Mapped[str] = mapped_column(Text, default="")
    features: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="drafted", index=True)
    editor_notes: Mapped[list[str]] = mapped_column(default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    brief: Mapped[Brief | None] = relationship(back_populates="drafts")
    assets: Mapped[list[Asset]] = relationship(back_populates="draft")
    publication: Mapped[Publication | None] = relationship(back_populates="draft", uselist=False)


class Asset(Base):
    """A rendered visual for a draft, on disk and ready to upload."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text)
    alt_text: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    draft: Mapped[Draft] = relationship(back_populates="assets")


class Publication(Base):
    """A draft handed to the publishing provider (Typefully), and where it landed."""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), index=True, unique=True)
    provider: Mapped[str] = mapped_column(String(32), default="typefully")
    provider_draft_id: Mapped[str | None] = mapped_column(String(64), index=True)
    post_id: Mapped[str | None] = mapped_column(String(64), index=True)
    post_url: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    draft: Mapped[Draft] = relationship(back_populates="publication")
    metrics: Mapped[list[PostMetric]] = relationship(back_populates="publication")


class ModelCall(Base):
    """One billed model call. Kept per agent so `xswarm cost` can answer "which stage
    is eating the budget" rather than just "we spent $X"."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[dt.date] = mapped_column(index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostMetric(Base):
    """One analytics snapshot of a published post. Kept as a time series so the
    Strategist can compare like-for-like at a fixed age (24h)."""

    __tablename__ = "post_metrics"
    __table_args__ = (
        UniqueConstraint("publication_id", "captured_at", name="uq_metric_snapshot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), index=True)
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    replies: Mapped[int] = mapped_column(Integer, default=0)
    reposts: Mapped[int] = mapped_column(Integer, default=0)
    quotes: Mapped[int] = mapped_column(Integer, default=0)
    bookmarks: Mapped[int] = mapped_column(Integer, default=0)
    link_clicks: Mapped[int] = mapped_column(Integer, default=0)
    profile_clicks: Mapped[int] = mapped_column(Integer, default=0)

    publication: Mapped[Publication] = relationship(back_populates="metrics")
