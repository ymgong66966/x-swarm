from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from anthropic.types import Usage as AnthropicUsage
from openai.types import CompletionUsage as OpenAIUsage

from .config import settings

log = logging.getLogger(__name__)
_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)

UsageLike = OpenAIUsage | AnthropicUsage | dict[str, int]


class LLMUnavailable(RuntimeError):
    pass


DEFAULT_SYSTEM = "You are a precise assistant. Answer exactly as instructed."


@dataclass(frozen=True)
class Usage:
    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    images: int = 0

    @property
    def cost_usd(self) -> float:
        prompt_rate, completion_rate = settings.model_prices.get(self.model, (0.0, 0.0))
        tokens = (
            self.prompt_tokens * prompt_rate + self.completion_tokens * completion_rate
        ) / 1_000_000
        return tokens + self.images * settings.image_prices.get(self.model, 0.0)


def resolve_provider() -> str | None:
    """Which provider we can actually talk to, or None if no key is configured."""
    choice = settings.llm_provider.lower()
    if choice == "openai":
        return "openai" if settings.openai_api_key else None
    if choice == "anthropic":
        return "anthropic" if settings.anthropic_api_key else None
    if settings.openai_api_key:
        return "openai"
    if settings.anthropic_api_key:
        return "anthropic"
    return None


class LLM:
    """Thin OpenAI/Anthropic wrapper with a dry-run mode.

    Dry run returns `None`, and every agent has a deterministic fallback path for that,
    so the whole pipeline can be exercised (and tested) without a key or spend.
    """

    def __init__(self, *, dry_run: bool = False) -> None:
        self.provider = resolve_provider()
        self.dry_run = dry_run or self.provider is None
        self._client = None
        # Every billed call, in order. Drained into `model_calls` by `costs.record`.
        self.usage: list[Usage] = []

    def _openai(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

    def _anthropic(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        strong: bool = False,
        max_tokens: int = 2000,
        agent: str = "unknown",
    ) -> str | None:
        if self.dry_run:
            return None
        system = system or DEFAULT_SYSTEM
        if self.provider == "openai":
            model = settings.openai_strong_model if strong else settings.openai_fast_model
            completion = self._openai().chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            self._track(agent, model, completion.usage)
            return completion.choices[0].message.content
        if self.provider == "anthropic":
            model = settings.strong_model if strong else settings.fast_model
            message = self._anthropic().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            self._track(agent, model, message.usage)
            return "".join(block.text for block in message.content if block.type == "text")
        raise LLMUnavailable("no model provider configured")

    def _track(self, agent: str, model: str, usage: UsageLike | None) -> None:
        """The two SDKs name the same two numbers differently."""
        if usage is None:
            return
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
            completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
        elif isinstance(usage, OpenAIUsage):
            prompt, completion = usage.prompt_tokens, usage.completion_tokens
        elif isinstance(usage, AnthropicUsage):
            prompt, completion = usage.input_tokens, usage.output_tokens
        else:
            log.warning("unrecognised usage payload from %s; not billing it", model)
            return
        self.usage.append(Usage(agent, model, int(prompt or 0), int(completion or 0)))

    def image(self, prompt: str, *, agent: str = "illustrator") -> bytes | None:
        """One generated image, or None when there is no image provider to call.

        Only OpenAI is wired up: Anthropic has no image generation, and a stream
        running on Anthropic falls back to the deterministic matplotlib templates.
        """
        if self.dry_run or self.provider != "openai":
            return None
        model = settings.image_model
        result = self._openai().images.generate(
            model=model,
            prompt=prompt,
            size=settings.image_size,
            quality=settings.image_quality,
            n=1,
        )
        data = result.data[0].b64_json if result.data else None
        if not data:
            log.warning("image provider returned no image data")
            return None
        self.usage.append(Usage(agent, model, 0, 0, images=1))
        return base64.b64decode(data)

    def complete_json(self, prompt: str, **kwargs: Any) -> Any | None:
        raw = self.complete(prompt, **kwargs)
        if raw is None:
            return None
        return parse_json(raw)


def parse_json(raw: str) -> Any | None:
    """Models wrap JSON in prose or fences more often than they should."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def load_prompt(name: str) -> str:
    return (settings.prompts_dir / f"{name}.md").read_text()
