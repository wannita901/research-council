"""Named retrieval sources + per-round assembly into a HybridRetrieval (plan/8).

`build_retrieval` returns REAL adapters (network); `build_stub_retrieval` returns
deterministic offline placeholders (used by the stub/offline path and tests).
Sources not yet implemented (semanticscholar/github/paperswithcode/web) fall back
to a stub so selecting them still works — transparently a placeholder.
"""

from __future__ import annotations

from research_council.retrieval.arxiv import ArxivProvider
from research_council.retrieval.github import GitHubProvider
from research_council.retrieval.hybrid import HybridRetrieval
from research_council.retrieval.openalex import OpenAlexProvider
from research_council.retrieval.semanticscholar import SemanticScholarProvider
from research_council.retrieval.wiki import WikiProvider
from research_council.store.models import Paper

# `paperswithcode` is intentionally NOT registered: its public API is Cloudflare/anti-bot
# gated (serves HTML to non-browsers). The adapter file is kept at retrieval/paperswithcode.py
# — re-add it here if the API becomes reachable. `web` is a stub until a search-API key is wired.
KNOWN_TOOLS = ["wiki", "openalex", "arxiv", "semanticscholar", "github", "web"]
_REAL = {
    "wiki": WikiProvider,
    "openalex": OpenAlexProvider,
    "arxiv": ArxivProvider,
    "semanticscholar": SemanticScholarProvider,
    "github": GitHubProvider,
}


class StubRetrieval:
    """Deterministic placeholder for any named source (offline / not-yet-built)."""

    def __init__(self, name: str):
        self.name = name

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        n = min(k, 3)
        return [
            Paper(id=f"{self.name}:p{i}",
                  title=f"[{self.name}] result {i} for {query[:40]}",
                  abstract="(stub abstract)", source=self.name)
            for i in range(1, n + 1)
        ]


def real_tools() -> list[str]:
    """Connectable tools (have a real adapter) — for the setup menu. Excludes `web` stub."""
    return [t for t in KNOWN_TOOLS if t in _REAL]


def _validate(tools: list[str]) -> None:
    unknown = [t for t in tools if t not in KNOWN_TOOLS]
    if unknown:
        raise ValueError(f"unknown retrieval tools {unknown}; known: {KNOWN_TOOLS}")


def build_retrieval(tools: list[str]) -> HybridRetrieval:
    _validate(tools)
    return HybridRetrieval([(_REAL[t]() if t in _REAL else StubRetrieval(t)) for t in tools])


def build_stub_retrieval(tools: list[str]) -> HybridRetrieval:
    _validate(tools)
    return HybridRetrieval([StubRetrieval(t) for t in tools])
