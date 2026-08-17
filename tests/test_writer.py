from __future__ import annotations

from xswarm.agents import writer
from xswarm.llm import LLM


def test_fallback_writes_variants_without_llm(session, brief):
    drafts = writer.write(brief, LLM(dry_run=True), voice="", playbook="")
    assert len(drafts) == 3
    assert {d.features["hook_style"] for d in drafts} == {"curious", "number", "claim"}
    assert all(d.features["fallback"] for d in drafts)


def test_link_goes_to_the_reply_not_the_body(session, brief):
    drafts = writer.write(brief, LLM(dry_run=True), voice="", playbook="")
    assert all("http" not in d.body for d in drafts)
    assert all(d.link_reply.endswith("2401.00001") for d in drafts)
