"""`search` tool — queries the round's enabled retrieval set (plan/15 #2)."""

from __future__ import annotations

from research_council.retrieval.base import RetrievalProvider
from research_council.tools.base import ToolResult


class SearchTool:
    name = "search"
    description = (
        "Search the enabled literature/code sources for a query; returns top results with ids."
    )

    def __init__(self, retrieval: RetrievalProvider, k: int = 8):
        self.retrieval = retrieval
        self.k = k

    async def run(self, query: str) -> ToolResult:
        papers = await self.retrieval.search(query, self.k)
        lines = []
        for p in papers:
            # council-internal wiki notes are our own synthesis — flag so they're NOT prior art
            tag = " [council-internal · not external prior art]" if p.origin == "internal" else ""
            lines.append(
                f"[{p.source}]{tag} {p.id} · {p.title} ({p.year or '----'}) :: {p.abstract[:200]}"
            )
        return ToolResult(content="\n".join(lines) or "(no results)", refs=[p.id for p in papers])
