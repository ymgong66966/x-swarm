"""Visual templates.

Five deterministic renderers, all driven by a small validated spec so a model only
ever supplies data, never drawing code. Everything renders on a dark card at a
2:1-ish aspect ratio, which is what X shows uncropped in the timeline.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Literal

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrow, FancyBboxPatch  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from .config import settings  # noqa: E402

BACKGROUND = "#0d1117"
FOREGROUND = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#58a6ff"
ACCENT_ALT = "#f78166"
GRID = "#21262d"

TEMPLATES = ("result_chart", "comparison_table", "concept_diagram", "quote_card", "number_card")

Template = Literal[
    "result_chart", "comparison_table", "concept_diagram", "quote_card", "number_card"
]


class Series(BaseModel):
    label: str
    value: float
    highlight: bool = False


class VisualSpec(BaseModel):
    """What the Visualizer agent produces. Deliberately tiny: a model that can only
    emit labels and numbers cannot invent a misleading chart type."""

    template: Template = "quote_card"
    title: str = ""
    subtitle: str = ""
    unit: str = ""
    series: list[Series] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    body: str = ""
    number: str = ""
    caption: str = ""
    source: str = ""


def _figure() -> tuple[plt.Figure, plt.Axes]:
    dpi = 100
    fig = plt.figure(
        figsize=(settings.visual_width_px / dpi, settings.visual_height_px / dpi), dpi=dpi
    )
    fig.patch.set_facecolor(BACKGROUND)
    ax = fig.add_axes((0.06, 0.10, 0.88, 0.72))
    ax.set_facecolor(BACKGROUND)
    return fig, ax


def _chrome(fig: plt.Figure, spec: VisualSpec) -> None:
    if spec.title:
        fig.text(
            0.06,
            0.92,
            "\n".join(textwrap.wrap(spec.title, 62)[:2]),
            color=FOREGROUND,
            fontsize=26,
            fontweight="bold",
            va="top",
        )
    if spec.subtitle:
        fig.text(0.06, 0.855, spec.subtitle[:110], color=MUTED, fontsize=15, va="top")
    footer = spec.source or spec.caption
    if footer:
        fig.text(0.06, 0.035, footer[:120], color=MUTED, fontsize=12)


def _strip(ax: plt.Axes) -> None:
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])


def _result_chart(ax: plt.Axes, spec: VisualSpec) -> None:
    series = spec.series or [Series(label="n/a", value=0.0)]
    labels = ["\n".join(textwrap.wrap(s.label, 16)) for s in series]
    values = [s.value for s in series]
    colors = [ACCENT_ALT if s.highlight else ACCENT for s in series]
    bars = ax.bar(range(len(series)), values, color=colors, width=0.6)
    ax.set_xticks(range(len(series)))
    ax.set_xticklabels(labels, color=FOREGROUND, fontsize=14)
    ax.tick_params(axis="y", colors=MUTED, labelsize=12)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    span = max(values) - min(0.0, min(values)) or 1.0
    for bar, value in zip(bars, values, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + span * 0.02,
            f"{value:g}{spec.unit}",
            ha="center",
            color=FOREGROUND,
            fontsize=15,
            fontweight="bold",
        )
    ax.set_ylim(0, max(values) * 1.18 if max(values) > 0 else 1)


def _comparison_table(ax: plt.Axes, spec: VisualSpec) -> None:
    _strip(ax)
    columns = spec.columns or ["", ""]
    rows = spec.rows[:6] or [["", ""]]
    n_cols = len(columns)
    col_x = [0.02 + i * (0.96 / n_cols) for i in range(n_cols)]
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    top = 0.92
    for x, column in zip(col_x, columns, strict=False):
        ax.text(x, top, column[:26], color=ACCENT, fontsize=16, fontweight="bold", va="top")
    ax.plot([0.02, 0.98], [top - 0.07, top - 0.07], color=GRID, linewidth=2)
    step = min(0.14, (top - 0.10) / max(len(rows), 1))
    for r, row in enumerate(rows):
        y = top - 0.14 - r * step
        for x, cell in zip(col_x, row, strict=False):
            ax.text(x, y, str(cell)[:30], color=FOREGROUND, fontsize=15, va="top")
        if r < len(rows) - 1:
            ax.plot([0.02, 0.98], [y - step + 0.045] * 2, color=GRID, linewidth=1)


def _concept_diagram(ax: plt.Axes, spec: VisualSpec) -> None:
    _strip(ax)
    stages = spec.stages[:5] or ["input", "model", "output"]
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    gap = 0.06
    width = (0.96 - gap * (len(stages) - 1)) / len(stages)
    # Wrap to the box at the largest size where every stage still fits, and ellipsize
    # rather than clipping mid-word if a stage is longer than the box can hold.
    max_lines = 5
    # chars_per_unit is 1600px / measured glyph width at that size, minus padding.
    sizes = ((16, 120), (13, 150), (11, 175))
    fontsize = sizes[-1][0]
    wrapped = [textwrap.wrap(stage, max(10, int(width * sizes[-1][1]))) or [""] for stage in stages]
    for size, chars_per_unit in sizes:
        candidate = [
            textwrap.wrap(stage, max(10, int(width * chars_per_unit))) or [""] for stage in stages
        ]
        if max(len(w) for w in candidate) <= max_lines:
            fontsize, wrapped = size, candidate
            break
    for index, lines in enumerate(wrapped):
        if len(lines) > max_lines:
            wrapped[index] = lines[: max_lines - 1] + [lines[max_lines - 1].rstrip("-") + "…"]
    height, mid = 0.44, 0.5
    for index, lines in enumerate(wrapped):
        x = 0.02 + index * (width + gap)
        ax.add_patch(
            FancyBboxPatch(
                (x, mid - height / 2),
                width,
                height,
                boxstyle="round,pad=0.012",
                linewidth=2,
                edgecolor=ACCENT if index % 2 == 0 else ACCENT_ALT,
                facecolor="#161b22",
            )
        )
        ax.text(
            x + width / 2,
            mid,
            "\n".join(lines),
            ha="center",
            va="center",
            color=FOREGROUND,
            fontsize=fontsize,
            linespacing=1.4,
        )
        if index < len(stages) - 1:
            ax.add_patch(
                FancyArrow(
                    x + width + gap * 0.2,
                    mid,
                    gap * 0.6,
                    0,
                    width=0.004,
                    head_width=0.028,
                    head_length=gap * 0.3,
                    color=MUTED,
                    length_includes_head=True,
                )
            )


def _quote_card(ax: plt.Axes, spec: VisualSpec) -> None:
    _strip(ax)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    lines = textwrap.wrap(spec.body or spec.title, 46)[:7]
    # Shrink only when the text is long enough to overflow the card.
    size = 34 if len(lines) <= 4 else 28
    ax.plot([0.015, 0.015], [0.12, 0.88], color=ACCENT, linewidth=6, solid_capstyle="round")
    ax.text(
        0.06,
        0.5,
        "\n".join(lines),
        color=FOREGROUND,
        fontsize=size,
        va="center",
        linespacing=1.45,
    )
    if spec.caption:
        ax.text(0.06, 0.04, spec.caption[:90], color=ACCENT, fontsize=15)


def _number_card(ax: plt.Axes, spec: VisualSpec) -> None:
    _strip(ax)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    number = (spec.number or "—")[:18]
    ax.text(
        0.5,
        0.66,
        number,
        color=ACCENT,
        fontsize=150 if len(number) <= 6 else 96,
        fontweight="bold",
        ha="center",
        va="center",
    )
    body = "\n".join(textwrap.wrap(spec.body or spec.caption, 44)[:3])
    ax.text(
        0.5, 0.22, body, color=FOREGROUND, fontsize=26, ha="center", va="center", linespacing=1.4
    )


_RENDERERS = {
    "result_chart": _result_chart,
    "comparison_table": _comparison_table,
    "concept_diagram": _concept_diagram,
    "quote_card": _quote_card,
    "number_card": _number_card,
}


def render(spec: VisualSpec, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = _figure()
    try:
        _RENDERERS[spec.template](ax, spec)
        _chrome(fig, spec)
        fig.savefig(path, facecolor=BACKGROUND)
    finally:
        plt.close(fig)
    return path


def alt_text(spec: VisualSpec) -> str:
    """X requires alt text on media, and the Editor blocks drafts without it. Derived
    from the spec rather than the model so it always matches what was drawn."""
    head = spec.title or spec.body or spec.caption
    if spec.template == "result_chart":
        points = ", ".join(f"{s.label} {s.value:g}{spec.unit}" for s in spec.series)
        return f"Bar chart. {head}. {points}."[:1000]
    if spec.template == "comparison_table":
        return f"Table comparing {', '.join(spec.columns)}. {head}."[:1000]
    if spec.template == "concept_diagram":
        return f"Diagram: {' then '.join(spec.stages)}. {head}."[:1000]
    if spec.template == "number_card":
        return f"Card showing {spec.number}. {head}."[:1000]
    return f"Text card. {head}."[:1000]
