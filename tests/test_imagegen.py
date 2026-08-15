from __future__ import annotations

import base64
import datetime as dt
import io

import pytest
from conftest import make_draft
from PIL import Image

from xswarm import imagegen
from xswarm.agents import illustrator, visualizer
from xswarm.config import settings
from xswarm.imagegen import ArtSpec
from xswarm.llm import LLM, Usage
from xswarm.models import Article

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
        self.calls: list[dict[str, str]] = []

    def complete_json(self, prompt, **kwargs):
        return self.payload

    def image(self, prompt, *, agent="illustrator", model="", quality=""):
        self.prompts.append(prompt)
        self.calls.append({"model": model, "quality": quality})
        if self._image is None:
            return None
        self.usage.append(Usage(agent, model or settings.image_model, 0, 0, images=1))
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


def test_site_hero_drops_the_dark_ground_but_keeps_the_no_text_rule():
    """A near-black banner inside the site's cream article page would read as a hole."""
    prompt = imagegen.build_prompt(ArtSpec(style="site_hero", subject="A walker by a chair"))
    assert "#fbf9f5" in prompt
    assert "#0d1117" not in prompt
    assert "No text" in prompt


def test_a_site_photo_asks_for_people_and_drops_the_no_faces_rule():
    """The illustration constraints are what made caregiving banners pictures of empty
    furniture; a photograph of care has to be allowed to contain the carer."""
    prompt = imagegen.build_prompt(
        ArtSpec(style="site_photo", subject="A daughter steadying her father at the bedside")
    )
    assert "documentary" in prompt and "photograph of real caregiving" in prompt
    assert "No human faces" not in prompt
    assert "Flat editorial illustration" not in prompt
    assert "#0d1117" not in prompt
    assert "No text" in prompt
    assert "no name badges" in prompt


def test_a_photo_retry_does_not_ask_for_shapes_only(tmp_path):
    llm = TextReadingLLM(["YES", "NO"])
    imagegen.generate(ArtSpec(style="site_photo", subject="a bedside"), tmp_path / "a.png", llm)
    assert "Draw shapes only" not in llm.prompts[1]
    assert "Keep every surface blank" in llm.prompts[1]


def test_a_jpg_hero_is_re_encoded_and_capped_in_width(tmp_path, monkeypatch):
    """Photographs skip the flat-art palette squeeze, so the size has to come off here."""
    monkeypatch.setattr(settings, "hero_max_width", 800)
    buffer = io.BytesIO()
    Image.new("RGB", (1536, 1024), "white").save(buffer, "PNG")
    path = tmp_path / "hero.jpg"
    assert imagegen.generate(ArtSpec(subject="x"), path, FakeLLM(image=buffer.getvalue())) == path
    with Image.open(path) as written:
        assert written.format == "JPEG"
        assert written.size == (800, 533)


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


def test_an_article_hero_is_a_photograph_shot_on_the_better_model(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    article = Article(
        id=3,
        run_date=dt.date(2026, 8, 14),
        pillar="transitions_of_care",
        audience="caregiver",
        thesis="Discharge preparation lowers caregiver stress.",
        title="Be ready for hospital discharge",
        slug="be-ready-for-hospital-discharge",
        dek="What to ask for before the car ride home.",
        meta_description="Discharge training for dementia caregivers.",
        body_md="Unplanned discharges leave caregivers doing transfers they were never shown.",
        evidence=["Training before discharge is associated with lower caregiver strain."],
    )
    buffer = io.BytesIO()
    Image.new("RGB", (1536, 1024), "white").save(buffer, "PNG")
    llm = FakeLLM(
        {
            "subject": "A nurse kneeling to show a transfer grip while a daughter steadies her "
            "father at the edge of the bed",
            "emphasis": "the two pairs of hands on his forearm",
            "alt_text": "A nurse and a daughter helping an older man stand from a bed at home.",
        },
        image=buffer.getvalue(),
    )
    drawn = illustrator.illustrate_article(article, llm)
    assert drawn is not None
    path, alt = drawn
    assert path.suffix == ".jpg" and path.exists()
    assert alt.startswith("A nurse and a daughter")
    assert llm.calls[0] == {
        "model": settings.hero_image_model,
        "quality": settings.hero_image_quality,
    }
    assert "transfer grip" in llm.prompts[0]
    assert "No human faces" not in llm.prompts[0]
