from __future__ import annotations

import json
import re
from typing import Any

from .config import settings

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


class LLMUnavailable(RuntimeError):
    pass


DEFAULT_SYSTEM = "You are a precise assistant. Answer exactly as instructed."


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
    ) -> str | None:
        if self.dry_run:
            return None
        system = system or DEFAULT_SYSTEM
        if self.provider == "openai":
            completion = self._openai().chat.completions.create(
                model=settings.openai_strong_model if strong else settings.openai_fast_model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            return completion.choices[0].message.content
        if self.provider == "anthropic":
            message = self._anthropic().messages.create(
                model=settings.strong_model if strong else settings.fast_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in message.content if block.type == "text")
        raise LLMUnavailable("no model provider configured")

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
