from __future__ import annotations

import pytest

from xswarm.render import TEMPLATES, Series, VisualSpec, alt_text, render

SPECS = {
    "result_chart": VisualSpec(
        template="result_chart",
        title="Speculative tool calls cut latency",
        unit="s",
        series=[
            Series(label="baseline", value=4.1),
            Series(label="ours", value=1.3, highlight=True),
        ],
    ),
    "comparison_table": VisualSpec(
        template="comparison_table",
        title="What changes",
        columns=["", "before", "after"],
        rows=[["latency", "4.1s", "1.3s"], ["tokens", "same", "same"]],
    ),
    "concept_diagram": VisualSpec(
        template="concept_diagram",
        title="The loop",
        stages=["plan", "speculate", "verify", "commit"],
    ),
    "quote_card": VisualSpec(template="quote_card", body="Agents are mostly waiting on tools."),
    "number_card": VisualSpec(template="number_card", number="3.2x", body="lower latency"),
}


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_renders_a_png(template, tmp_path):
    path = render(SPECS[template], tmp_path / f"{template}.png")
    assert path.exists()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert path.stat().st_size > 5_000


def test_renders_with_empty_spec(tmp_path):
    """A model returning nothing useful must still produce a valid image."""
    for template in TEMPLATES:
        path = render(VisualSpec(template=template), tmp_path / f"empty-{template}.png")
        assert path.exists()


def test_alt_text_mentions_the_data_drawn():
    assert "baseline 4.1s" in alt_text(SPECS["result_chart"])
    assert "3.2x" in alt_text(SPECS["number_card"])
    assert "plan then speculate" in alt_text(SPECS["concept_diagram"])
