from __future__ import annotations

from conftest import make_draft

from xswarm.agents import visualizer
from xswarm.config import settings
from xswarm.llm import LLM


class FakeLLM(LLM):
    def __init__(self, payload):
        self.payload = payload
        self.dry_run = False
        self.provider = "fake"

    def complete_json(self, prompt, **kwargs):
        return self.payload


def test_model_spec_is_used(brief):
    draft = make_draft(brief, "body")
    spec = visualizer.build_spec(
        draft,
        brief,
        FakeLLM(
            {
                "template": "result_chart",
                "title": "Latency",
                "unit": "s",
                "series": [
                    {"label": "baseline", "value": 4.1},
                    {"label": "ours", "value": 1.3, "highlight": True},
                ],
            }
        ),
    )
    assert spec.template == "result_chart"
    assert spec.source == "arxiv"


def test_chart_with_one_series_falls_back(brief):
    """A one-bar 'comparison' is the classic way a model fabricates a result."""
    draft = make_draft(brief, "body")
    llm = FakeLLM({"template": "result_chart", "series": [{"label": "ours", "value": 1}]})
    spec = visualizer.build_spec(draft, brief, llm)
    assert spec.template == "number_card"


def test_malformed_spec_falls_back(brief):
    draft = make_draft(brief, "body")
    spec = visualizer.build_spec(draft, brief, FakeLLM({"template": "not_a_template"}))
    assert spec.template == "number_card"
    assert spec.number == "3.2"


def test_dry_run_still_renders_and_sets_alt_text(session, brief, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    draft = make_draft(brief, "body")
    session.add(draft)
    session.flush()
    assets = visualizer.run(session, LLM(dry_run=True), [draft])
    assert len(assets) == 1
    assert draft.alt_text == assets[0].alt_text
    assert assets[0].alt_text
    assert (tmp_path / f"draft-{draft.id}-{assets[0].kind}.png").exists()


def test_only_one_variant_per_brief_is_rendered(session, brief, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    drafts = []
    for variant in (1, 0, 2):
        draft = make_draft(brief, f"body {variant}")
        draft.variant = variant
        session.add(draft)
        drafts.append(draft)
    session.flush()
    assets = visualizer.run(session, LLM(dry_run=True), drafts)
    assert len(assets) == 1
    assert assets[0].draft_id == next(d.id for d in drafts if d.variant == 0)
