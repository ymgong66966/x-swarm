from __future__ import annotations

from conftest import make_draft
from sqlalchemy.orm import Session

from xswarm import memory
from xswarm.models import Brief


def test_only_committed_drafts_count_as_memory(session: Session, brief: Brief):
    considered = make_draft(brief, "a draft nobody approved", features={"pillar": "x"})
    considered.status = "ready_for_review"
    session.add(considered)
    session.flush()
    assert memory.committed_posts(session) == []

    considered.status = "approved"
    session.flush()
    posts = memory.committed_posts(session)
    assert len(posts) == 1
    assert posts[0].title == brief.candidate.item.title


def test_repeat_topic_is_detected(session: Session, brief: Brief):
    draft = make_draft(brief, "shipped", features={"pillar": "paper_of_the_day"})
    draft.status = "published"
    session.add(draft)
    session.flush()

    covered = memory.covered_titles(session)
    assert memory.is_repeat("Speculative tool calling for LLM agents", covered)
    assert not memory.is_repeat("A benchmark for long-horizon agent memory", covered)


def test_writer_context_is_empty_on_a_cold_start(session: Session):
    assert memory.writer_context(session) == "(nothing posted yet)"
