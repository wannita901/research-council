"""Official first-party SDK adapters (plan/2 §7 — chosen over LiteLLM for supply-chain).

Thin wrappers behind LLMProvider. Imports are guarded so the offline path never
requires the SDKs. TODO(incr): real calls + retries + token/cost accounting.
"""

from __future__ import annotations

import os

from research_council.providers.base import Response, Usage


class _BaseSDKProvider:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:  # pragma: no cover
        raise NotImplementedError(
            f"{self.name} live adapter not implemented yet (increment). "
            "Use the offline stub path, or implement the SDK call here."
        )


class OpenAIProvider(_BaseSDKProvider):
    name = "openai"
    env_key = "OPENAI_API_KEY"


class AnthropicProvider(_BaseSDKProvider):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"


class GeminiProvider(_BaseSDKProvider):
    name = "gemini"
    env_key = "GEMINI_API_KEY"


_REGISTRY = {p.name: p for p in (OpenAIProvider, AnthropicProvider, GeminiProvider)}


def build_provider(vendor: str, model: str):
    cls = _REGISTRY.get(vendor)
    if cls is None:
        raise ValueError(f"unknown vendor {vendor!r}; known: {sorted(_REGISTRY)}")
    if not os.getenv(cls.env_key):
        raise RuntimeError(f"{cls.env_key} not set — cannot run {vendor} live")
    return cls(model)
