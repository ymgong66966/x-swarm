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

    # USD per 1M tokens, (prompt, completion). Unknown models cost 0 rather than
    # guessing; update this when you switch models.
    model_prices: dict[str, tuple[float, float]] = Field(
        default={
            "gpt-4.1": (2.00, 8.00),
            "gpt-4.1-mini": (0.40, 1.60),
            "claude-sonnet-4-5": (3.00, 15.00),
            "claude-3-5-haiku-latest": (0.80, 4.00),
        }
    )
    monthly_budget_usd: float = 20.0

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
            # Hedging tics the account owner reads as posturing.
            "i checked",
            "i didn't check",
            "i don't fully know",
            "worth a look",
        ]
    )

    # Threads. Deliberately narrow: a thread is only worth a reader's time when the
    # brief has enough verified material to carry it.
    thread_pillars: list[str] = Field(default=["paper_of_the_day", "explainer"])
    thread_min_claims: int = 4
    max_thread_posts: int = 5
    roundup_picks: int = 5

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

    # ------------------------------------------------------------------ care stream
    # The company site is the only allowed source of product claims.
    care_site_url: str = "https://alvernahealth.com"
    care_site_paths: list[str] = Field(default=["/", "/providers", "/become-a-trainer"])
    # Where articles are published, used for internal links, promo links and SEO checks.
    care_blog_base_url: str = "https://alvernahealth.com/resources"

    # Publishing to the company site: an approved article becomes a pull request there,
    # and a human merging that PR is what puts it live.
    site_repo: str = "alverna-health/alverna-site"
    site_repo_url: str = "https://github.com/alverna-health/alverna-site.git"
    site_repo_dir: Path = REPO_ROOT.parent / "alverna-site"
    site_default_branch: str = "main"
    site_content_dir: str = "content/resources"
    site_media_dir: str = "public/resources/media"
    site_branch_prefix: str = "article"
    # Only needed to open the PR through the API; without it the branch is still pushed
    # and the compare URL is printed for a human to click.
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    care_articles_dir: Path = REPO_ROOT / "content" / "articles"
    care_articles_per_run: int = 2
    care_candidates_per_run: int = 6
    care_evidence_per_article: int = 6
    care_min_words: int = 850
    care_max_words: int = 1500
    care_promos_per_article: int = 3

    # Regulatory claims are only allowed to cite these hosts.
    care_authoritative_hosts: list[str] = Field(
        default=[
            "cms.gov",
            "medicare.gov",
            "federalregister.gov",
            "ecfr.gov",
            "hhs.gov",
            "medicaid.gov",
            "congress.gov",
            "gao.gov",
            "nih.gov",
            "ncbi.nlm.nih.gov",
            "cdc.gov",
        ]
    )
    # Discussion platforms: usable as motivation and sentiment, never as evidence.
    care_signal_hosts: list[str] = Field(
        default=["reddit.com", "linkedin.com", "quora.com", "facebook.com", "x.com"]
    )
    care_subreddits: list[str] = Field(
        default=["CaregiverSupport", "dementia", "AgingParents", "HomeHealthcare"]
    )
    care_news_queries: list[str] = Field(
        default=[
            "Medicare caregiver training services",
            "family caregiver policy",
            "telehealth Medicare policy",
            "hospital discharge caregiver readmission",
            '"caregiver training" site:linkedin.com',
        ]
    )
    care_policy_feeds: list[str] = Field(
        default=[
            "https://www.kff.org/feed/",
            "https://homehealthcarenews.com/feed/",
            "https://www.healthaffairs.org/action/showFeed?type=etoc&feed=rss&jc=hlthaff",
        ]
    )
    care_research_queries: list[str] = Field(
        default=[
            "caregiver training intervention outcomes",
            "telehealth caregiver education randomized",
            "family caregiver burden readmission",
        ]
    )
    care_source_max_age_days: int = 45
    care_pillars: list[str] = Field(
        default=[
            "policy_explainer",
            "reimbursement_mechanics",
            "caregiver_skills",
            "transitions_of_care",
            "clinician_career",
            "field_signal",
        ]
    )
    care_audiences: list[str] = Field(default=["provider", "clinician", "caregiver"])
    # Phrases that must never appear in care content, whatever the model decides.
    # Matched on word boundaries, so "secure" and "procedure" are safe.
    care_banned_phrases: list[str] = Field(
        default=[
            "guaranteed reimbursement",
            "guaranteed payment",
            "guaranteed coverage",
            "always covered",
            "always reimbursable",
            "fully covered by medicare",
            "cure",
            "cures",
            "risk-free",
            "miracle",
            "clinically proven",
            "medically proven",
            "best in class",
            "revolutionary",
            "life-changing",
        ]
    )
    care_disclaimer: str = (
        "This article is general information for education, not medical or billing advice. "
        "Coverage, coding, and documentation requirements change; confirm current rules with "
        "CMS and your payer, and route clinical questions to the treating clinician."
    )

    # Analytics for the blog side. Without a key the dashboard just omits traffic.
    plausible_api_key: str | None = None
    plausible_site_id: str | None = None
    plausible_base_url: str = "https://plausible.io/api/v1"
    dashboard_path: Path = REPO_ROOT / "dashboard.html"
    dashboard_lookback_days: int = 30

    # Measurement / strategy
    metrics_lookback_days: int = 14
    strategy_min_posts: int = 8

    voice_path: Path = REPO_ROOT / "voice.md"
    playbook_path: Path = REPO_ROOT / "playbook.md"
    prompts_dir: Path = REPO_ROOT / "prompts"


settings = Settings()
