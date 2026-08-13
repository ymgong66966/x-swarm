from __future__ import annotations

import pytest

from xswarm.config import settings
from xswarm.llm import LLM, parse_json, resolve_provider


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "llm_provider", "auto")


def test_provider_prefers_openai_then_anthropic(no_keys, monkeypatch):
    assert resolve_provider() is None
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    assert resolve_provider() == "anthropic"
    monkeypatch.setattr(settings, "openai_api_key", "k")
    assert resolve_provider() == "openai"
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    assert resolve_provider() == "anthropic"


def test_no_key_means_dry_run(no_keys):
    assert LLM().dry_run is True


def test_parse_json_handles_fenced_and_prefixed_output():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! [{"a": 1}]') == [{"a": 1}]
    assert parse_json("not json at all") is None


def test_dry_run_returns_none():
    llm = LLM(dry_run=True)
    assert llm.complete("hi") is None
    assert llm.complete_json("hi") is None
