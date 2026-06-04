"""Provider abstraction (plan/2 §2). One interface; vendor SDKs behind adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class Response(BaseModel):
    text: str
    usage: Usage = Usage()


@runtime_checkable
class LLMProvider(Protocol):
    """Uniform chat interface. Real adapters add retries/caching/cost here."""

    name: str   # vendor: "openai" | "anthropic" | "gemini"
    model: str

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:
        ...
