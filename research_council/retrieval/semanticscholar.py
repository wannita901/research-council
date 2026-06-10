"""Semantic Scholar Graph API adapter (plan/8). Key optional (higher rate limits)."""

from __future__ import annotations

import asyncio
import os

from research_council.store.models import Paper

SS = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,year,url"


class SemanticScholarProvider:
    name = "semanticscholar"

    def __init__(self):
        self.key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        import httpx

        # Works unauthenticated via a shared pool that often 429s — back off and retry.
        headers = {"x-api-key": self.key} if self.key else {}
        params = {"query": query, "limit": min(k, 100), "fields": FIELDS}
        data = None
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers) as c:
                for attempt in range(3):
                    r = await c.get(SS, params=params)
                    if r.status_code == 429 and attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    data = r.json()
                    break
        except Exception:
            return []
        if data is None:
            return []
        out: list[Paper] = []
        for w in (data.get("data") or [])[:k]:
            out.append(
                Paper(
                    id=w.get("paperId") or "(semanticscholar)",
                    title=w.get("title") or "(untitled)",
                    abstract=w.get("abstract") or "",
                    year=w.get("year"),
                    url=w.get("url"),
                    source="semanticscholar",
                )
            )
        return out
