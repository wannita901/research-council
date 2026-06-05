"""Official first-party SDK adapters (plan/2 §7).

Each adapter wraps one vendor SDK behind LLMProvider, with a shared retry/backoff
and token→cost accounting. SDK imports are LAZY (inside the cached client) so the
package — and the whole offline path — never requires these packages installed.

Install with:  pip install -e ".[providers]"
"""

from __future__ import annotations

import asyncio
import os
from functools import cached_property

from research_council.providers.base import Response, Usage

# Approximate prices, USD per 1M tokens (input, output). Edit to taste; unknown -> free.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5": (1.25, 10.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "gemini-2.5-pro": (1.25, 10.0),
}


def _cost(model: str, prompt: int, completion: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return round(prompt / 1e6 * pin + completion / 1e6 * pout, 6)


async def _retry(fn, attempts: int = 4, base: float = 0.5):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:  # transient API/network errors → backoff
            last = e
            if i < attempts - 1:
                await asyncio.sleep(base * (2**i))
    assert last is not None
    raise last


class _BaseSDKProvider:
    name = "base"
    env_key = ""

    def __init__(self, model: str, max_tokens: int = 4096):
        self.model = model
        self.max_tokens = max_tokens


class OpenAIProvider(_BaseSDKProvider):
    name = "openai"
    env_key = "OPENAI_API_KEY"

    @cached_property
    def client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI()

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:
        async def call():
            return await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )

        r = await _retry(call)
        u = r.usage
        return Response(
            text=r.choices[0].message.content or "",
            usage=Usage(
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                cost_usd=_cost(self.model, u.prompt_tokens, u.completion_tokens),
            ),
        )


class AnthropicProvider(_BaseSDKProvider):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"

    @cached_property
    def client(self):
        from anthropic import AsyncAnthropic

        return AsyncAnthropic()

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:
        async def call():
            return await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )

        r = await _retry(call)
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return Response(
            text=text,
            usage=Usage(
                prompt_tokens=r.usage.input_tokens,
                completion_tokens=r.usage.output_tokens,
                cost_usd=_cost(self.model, r.usage.input_tokens, r.usage.output_tokens),
            ),
        )


class GeminiProvider(_BaseSDKProvider):
    name = "gemini"
    env_key = "GEMINI_API_KEY"

    @cached_property
    def client(self):
        from google import genai

        return genai.Client(api_key=os.getenv(self.env_key))

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:
        from google.genai import types

        async def call():
            return await self.client.aio.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(system_instruction=system),
            )

        r = await _retry(call)
        um = getattr(r, "usage_metadata", None)
        pin = getattr(um, "prompt_token_count", 0) or 0
        pout = getattr(um, "candidates_token_count", 0) or 0
        return Response(
            text=r.text or "",
            usage=Usage(prompt_tokens=pin, completion_tokens=pout, cost_usd=_cost(self.model, pin, pout)),
        )


_REGISTRY = {p.name: p for p in (OpenAIProvider, AnthropicProvider, GeminiProvider)}


def build_provider(vendor: str, model: str):
    cls = _REGISTRY.get(vendor)
    if cls is None:
        raise ValueError(f"unknown vendor {vendor!r}; known: {sorted(_REGISTRY)}")
    if not os.getenv(cls.env_key):
        raise RuntimeError(f"{cls.env_key} not set — cannot run {vendor} live")
    return cls(model)
