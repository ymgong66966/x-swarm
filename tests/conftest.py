from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from xswarm.models import Base, Brief, Candidate, Draft, Item


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session


@pytest.fixture()
def brief(session: Session) -> Brief:
    item = Item(
        fingerprint="fp1",
        source="arxiv",
        external_id="2401.00001",
        url="https://arxiv.org/abs/2401.00001",
        title="Speculative tool calling for LLM agents",
        summary="We show 3.2x lower end-to-end latency on the AgentBench subset.",
    )
    session.add(item)
    session.flush()
    candidate = Candidate(item_id=item.id, run_date=dt.date.today(), score=0.8)
    session.add(candidate)
    session.flush()
    brief = Brief(
        candidate_id=candidate.id,
        whats_new="Speculative execution of tool calls in agent loops.",
        key_number="3.2x lower end-to-end latency",
        caveat="Only evaluated on one benchmark subset.",
        builder_takeaway="Worth trying if your agent is dominated by sequential tool latency.",
        grounded_claims=["3.2x lower end-to-end latency on the AgentBench subset"],
        unverified_claims=["Works for any agent framework"],
    )
    session.add(brief)
    session.flush()
    return brief


def make_draft(brief: Brief, body: str, **kwargs) -> Draft:
    return Draft(brief_id=brief.id, brief=brief, body=body, variant=0, **kwargs)
