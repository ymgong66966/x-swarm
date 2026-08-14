from __future__ import annotations

import base64

import pytest
from conftest import make_draft

from xswarm import imagegen
from xswarm.agents import illustrator, visualizer
from xswarm.config import settings
from xswarm.imagegen import ArtSpec
from xswarm.llm import LLM, Usage

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeLLM(LLM):
    """Answers the Illustrator, and pretends to be an image provider."""

    def __init__(self, payload=None, *, image: bytes | None = PNG):
        self.payload = payload
        self._image = image
        self.dry_run = False
        self.provider = "fake"
        self.usage: list[Usage] = []
        self.prompts: list[str] = []

    def complete_json(self, prompt, **kwargs):
        return self.payload

    def image(self, prompt, *, agent="illustrator"):
        self.prompts.append(prompt)
        if self._image is None:
            return None
        self.usage.append(Usage(agent, settings.image_model, 0, 0, images=1))
        return self._image


def test_style_block_is_read_from_the_art_direction_file():
    block = imagegen.style_block("risk_dark")
    assert "fracturing" in block or "fragile" in block
    assert imagegen.style_block("not_a_style") == ""


def test_prompt_carries_subject_style_and_the_no_text_constraint():
    prompt = imagegen.build_prompt(
        ArtSpec(style="data_poster", subject="Ascending bars", emphasis="the final bar")
    )
    assert "Ascending bars" in prompt
    assert "the final bar" in prompt
    assert "poster geometry" in prompt  # the named style
    assert "#0d1117" in prompt  # the house style
    assert "No text" in prompt


def test_dry_run_generates_nothing(tmp_path):
    llm = LLM(dry_run=True)
    assert imagegen.generate(ArtSpec(subject="x"), tmp_path / "a.png", llm) is None


def test_generate_writes_the_returned_bytes(tmp_path):
    path = tmp_path / "nested" / "a.png"
    assert imagegen.generate(ArtSpec(subject="x"), path, FakeLLM()) == path
    assert path.read_bytes() == PNG


def test_image_usage_is_priced_per_image():
    usage = Usage("illustrator", "gpt-image-1", 0, 0, images=2)
    assert usage.cost_usd == pytest.approx(2 * settings.image_prices["gpt-image-1"])


def test_openai_image_call_is_decoded_and_tracked(monkeypatch):
    class FakeImages:
        def generate(self, **kwargs):
            self.kwargs = kwargs
            return type(
                "R",
                (),
                {"data": [type("D", (), {"b64_json": base64.b64encode(PNG).decode()})()]},
            )()

    images = FakeImages()
    llm = LLM(dry_run=False)
    llm.provider = "openai"
    llm.dry_run = False
    monkeypatch.setattr(llm, "_openai", lambda: type("C", (), {"images": images})())
    assert llm.image("a prompt") == PNG
    assert images.kwargs["model"] == settings.image_model
    assert llm.usage[0].images == 1


def test_illustrator_uses_the_model_spec(session, brief):
    draft = make_draft(brief, "body")
    session.add(draft)
    session.flush()
    llm = FakeLLM(
        {
            "style": "risk_dark",
            "subject": "A fracturing lattice",
            "emphasis": "the crack",
            "alt_text": "A dark lattice with one fractured strut.",
        }
    )
    asset = illustrator.illustrate(session, draft, llm)
    assert asset is not None
    assert asset.kind == "generated_art"
    assert asset.spec["style"] == "risk_dark"
    assert draft.alt_text == "A dark lattice with one fractured strut."
    assert "A fracturing lattice" in llm.prompts[0]


def test_unknown_style_falls_back_to_a_known_one(session, brief):
    draft = make_draft(brief, "body", features={"pillar": "counterpoint"})
    session.add(draft)
    session.flush()
    spec = illustrator.build_spec(
        draft, brief, FakeLLM({"style": "vaporwave", "subject": "A lattice"})
    )
    assert spec.style in settings.art_styles


def test_no_image_provider_returns_no_asset(session, brief):
    draft = make_draft(brief, "body")
    session.add(draft)
    session.flush()
    assert illustrator.illustrate(session, draft, FakeLLM({}, image=None)) is None


def test_auto_mode_plots_numbers_and_illustrates_everything_else(session, brief, monkeypatch):
    monkeypatch.setattr(settings, "visual_mode", "auto")
    monkeypatch.setattr(settings, "assets_dir", settings.assets_dir)
    assert visualizer.has_plottable_data(brief) is True
    brief.key_number = ""
    brief.visual_hint = "concept_diagram"
    assert visualizer.has_plottable_data(brief) is False


def test_generation_failure_still_renders_a_card(session, brief, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "visual_mode", "generate")
    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    draft = make_draft(brief, "body")
    session.add(draft)
    session.flush()
    asset = visualizer.attach_visual(session, draft, FakeLLM({}, image=None))
    assert asset.kind in ("number_card", "quote_card")


def test_a_forced_style_overrides_what_the_model_picked(session, brief):
    draft = make_draft(brief, "body")
    draft.features = {"art_style": "clinical_calm"}
    spec = illustrator.build_spec(
        draft, brief, FakeLLM({"style": "risk_dark", "subject": "A lattice"})
    )
    assert spec.style == "clinical_calm"
    assert spec.subject == "A lattice"


def test_image_cost_is_billed_to_the_stream_that_asked(session, brief, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    draft = make_draft(brief, "body")
    session.add(draft)
    session.flush()
    llm = FakeLLM({"style": "risk_dark", "subject": "A lattice"}, image=PNG)
    illustrator.illustrate(session, draft, llm)
    assert {u.agent for u in llm.usage} == {"illustrator_ml"}


class TextReadingLLM(FakeLLM):
    """An image provider whose output the vision check can read words in."""

    def __init__(self, verdicts: list[str]):
        super().__init__({})
        self.verdicts = verdicts
        self.looked: list[bytes] = []

    def look(self, image, question, *, agent="illustrator"):
        self.looked.append(image)
        return self.verdicts.pop(0)


def test_an_image_with_text_in_it_is_retried(tmp_path):
    llm = TextReadingLLM(["YES", "NO"])
    path = imagegen.generate(ArtSpec(subject="a lattice"), tmp_path / "art.png", llm)
    assert path is not None and path.exists()
    assert len(llm.prompts) == 2
    assert "previous attempt contained written words" in llm.prompts[1]


def test_an_image_that_keeps_its_text_is_not_used(tmp_path):
    llm = TextReadingLLM(["YES", "YES"])
    assert imagegen.generate(ArtSpec(subject="a lattice"), tmp_path / "art.png", llm) is None
    assert not (tmp_path / "art.png").exists()
