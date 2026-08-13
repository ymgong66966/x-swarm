from __future__ import annotations

from anthropic.types import Usage as AnthropicUsage
from openai.types import CompletionUsage as OpenAIUsage
from sqlalchemy.orm import Session

from xswarm import costs
from xswarm.llm import LLM, Usage
from xswarm.models import ModelCall


def test_cost_uses_the_price_table():
    usage = Usage("writer", "gpt-4.1", prompt_tokens=1_000_000, completion_tokens=500_000)
    assert usage.cost_usd == 2.00 + 4.00


def test_unknown_model_costs_nothing_rather_than_guessing():
    assert Usage("writer", "some-new-model", 1_000_000, 1_000_000).cost_usd == 0.0


def test_track_reads_both_sdk_shapes():
    llm = LLM(dry_run=True)
    llm._track(
        "writer",
        "gpt-4.1",
        OpenAIUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )
    llm._track(
        "editor",
        "claude-sonnet-4-5",
        AnthropicUsage(input_tokens=5, output_tokens=7),
    )
    llm._track("curator", "gpt-4.1-mini", None)
    assert [(u.agent, u.prompt_tokens, u.completion_tokens) for u in llm.usage] == [
        ("writer", 10, 20),
        ("editor", 5, 7),
    ]


def test_record_drains_usage_into_the_database(session: Session):
    llm = LLM(dry_run=True)
    llm.usage = [
        Usage("writer", "gpt-4.1", 1_000_000, 0),
        Usage("curator", "gpt-4.1-mini", 1_000_000, 0),
    ]
    total = costs.record(session, llm)

    assert round(total, 3) == 2.40
    assert llm.usage == []
    assert session.query(ModelCall).count() == 2
    assert costs.by_agent(session)[0][0] == "writer"
    assert round(costs.month_to_date(session), 3) == 2.40
