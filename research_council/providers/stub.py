"""Deterministic offline provider — lets the provider layer be tested without keys.

NOTE: the offline *debate* path uses StubPeer (agents/stub_peer.py), which bypasses
the provider entirely. This stub exists for provider-layer tests and `--live` parity.
"""

from __future__ import annotations

from research_council.providers.base import LLMProvider, Response, Usage


class StubProvider:
    def __init__(self, name: str, model: str = "stub"):
        self.name = name
        self.model = model

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:
        text = f"[stub:{self.name}] kind={kind or 'chat'} :: {user[:60]}"
        return Response(text=text, usage=Usage(prompt_tokens=len(user) // 4))


def _assert_protocol() -> None:
    assert isinstance(StubProvider("openai"), LLMProvider)
