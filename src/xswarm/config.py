from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration. Everything has a safe default so the pipeline can be
    exercised end-to-end with `--dry-run` before any credential exists."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="XSWARM_", extra="ignore")

    database_url: str = f"sqlite:///{REPO_ROOT / 'xswarm.db'}"

    # Model provider. "auto" picks OpenAI when its key is present, else Anthropic.
    llm_provider: str = "auto"
    anthropic_api_key: str | None = None
    fast_model: str = "claude-3-5-haiku-latest"
    strong_model: str = "claude-sonnet-4-5"
    openai_api_key: str | None = None
    openai_fast_model: str = "gpt-4.1-mini"
    openai_strong_model: str = "gpt-4.1"

    semantic_scholar_api_key: str | None = None
    github_token: str | None = None
    hf_token: str | None = None

    # Ingestion
    arxiv_categories: list[str] = Field(default=["cs.LG", "cs.CL", "cs.AI", "cs.MA"])
    arxiv_max_results: int = 60
    newsletter_feeds: list[str] = Field(
        default=[
            "https://openai.com/news/rss.xml",
            "https://deepmind.google/blog/rss.xml",
            "https://blog.google/innovation-and-ai/technology/ai/rss/",
            "https://jack-clark.net/feed/",
            "https://importai.substack.com/feed",
            "https://buttondown.com/ainews/rss",
            "https://simonwillison.net/atom/everything/",
            "https://bair.berkeley.edu/blog/feed.xml",
        ]
    )
    github_trending_languages: list[str] = Field(default=["python"])
    watch_repos: list[str] = Field(
        default=["vllm-project/vllm", "langchain-ai/langgraph", "huggingface/transformers"]
    )

    # Curation
    candidates_per_day: int = 8
    briefs_per_day: int = 4
    drafts_per_brief: int = 3
    repeat_topic_cooldown_days: int = 14
    novelty_window_days: int = 90

    # Content rules enforced by the Editor
    max_post_chars: int = 270
    banned_phrases: list[str] = Field(
        default=[
            "game changer",
            "game-changer",
            "mind-blowing",
            "🚨",
            "a thread 🧵",
            "let that sink in",
            "the future is here",
            "revolutionize",
            "delve",
        ]
    )

    # Visuals
    assets_dir: Path = REPO_ROOT / "assets"
    visual_width_px: int = 1600
    visual_height_px: int = 900

    # Publishing (Typefully v2). Without a key the Publisher stays in dry-run.
    typefully_api_key: str | None = None
    typefully_social_set_id: str | None = None
    typefully_base_url: str = "https://api.typefully.com/v2"
    publish_timezone: str = "America/New_York"
    # Local-time slots the Publisher schedules into, in order.
    publish_slots: list[str] = Field(default=["08:45", "12:30", "17:00"])
    publish_jitter_minutes: int = 7
    # Pillars that may be scheduled without a human approving them first.
    autopublish_pillars: list[str] = Field(default=[])

    # Measurement / strategy
    metrics_lookback_days: int = 14
    strategy_min_posts: int = 8

    voice_path: Path = REPO_ROOT / "voice.md"
    playbook_path: Path = REPO_ROOT / "playbook.md"
    prompts_dir: Path = REPO_ROOT / "prompts"


settings = Settings()
