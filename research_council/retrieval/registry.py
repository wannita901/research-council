"""Named retrieval sources + per-round assembly into a HybridRetrieval (plan/8).

Increment 1 ships deterministic stub sources so the debate runs offline. Each real
adapter (openalex/arxiv/semanticscholar/github/paperswithcode/web) replaces its stub
behind the same interface. `web` is excluded from eval batches (non-reproducible).
"""

from __future__ import annotations

from research_council.retrieval.hybrid import HybridRetrieval
from research_council.store.models import Paper

# All sources currently in the menu (plan/8). TODO(incr): swap stubs for real adapters.
KNOWN_TOOLS = [
    "wiki", "openalex", "arxiv", "semanticscholar", "github", "paperswithcode", "web",
]


class StubRetrieval:
    """Deterministic placeholder for any named source."""

    def __init__(self, name: str):
        self.name = name

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        n = min(k, 3)
        return [
            Paper(
                id=f"{self.name}:p{i}",
                title=f"[{self.name}] result {i} for {query[:40]}",
                abstract="(stub abstract)",
                source=self.name,
            )
            for i in range(1, n + 1)
        ]


def build_retrieval(tools: list[str]) -> HybridRetrieval:
    unknown = [t for t in tools if t not in KNOWN_TOOLS]
    if unknown:
        raise ValueError(f"unknown retrieval tools {unknown}; known: {KNOWN_TOOLS}")
    return HybridRetrieval([StubRetrieval(t) for t in tools])
