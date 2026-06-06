"""`search` tool — queries the round's enabled retrieval set (plan/15 #2)."""

from __future__ import annotations

from research_council.retrieval.base import RetrievalProvider
from research_council.tools.base import ToolResult


class SearchTool:
    name = "search"
    description = "Search the enabled literature/code sources for a query; returns top results with ids."

    def __init__(self, retrieval: RetrievalProvider, k: int = 8):
        self.retrieval = retrieval
        self.k = k

    async def run(self, query: str) -> ToolResult:
        papers = await self.retrieval.search(query, self.k)
        lines = [
            f"[{p.source}] {p.id} · {p.title} ({p.year or '----'}) :: {p.abstract[:200]}"
            for p in papers
        ]
        return ToolResult(content="\n".join(lines) or "(no results)", refs=[p.id for p in papers])
