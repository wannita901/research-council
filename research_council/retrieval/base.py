"""Retrieval abstraction (plan/8). Sources are selectable per round."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_council.store.models import Paper


@runtime_checkable
class RetrievalProvider(Protocol):
    name: str

    async def search(self, query: str, k: int = 10) -> list[Paper]: ...
