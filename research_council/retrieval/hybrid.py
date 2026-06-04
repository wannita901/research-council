"""Fan out to the enabled sources in parallel; dedup; provenance-tag (plan/8 §3)."""

from __future__ import annotations

import asyncio

from research_council.retrieval.base import RetrievalProvider
from research_council.store.models import Paper


class HybridRetrieval:
    def __init__(self, providers: list[RetrievalProvider]):
        self.providers = providers
        self.name = "hybrid(" + ",".join(p.name for p in providers) + ")"

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        results = await asyncio.gather(
            *(p.search(query, k) for p in self.providers), return_exceptions=True
        )
        seen: set[str] = set()
        out: list[Paper] = []
        for res in results:
            if isinstance(res, Exception):
                continue  # a dead source degrades the set rather than failing the run
            for paper in res:
                key = (paper.title or paper.id).strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(paper)
        return out
