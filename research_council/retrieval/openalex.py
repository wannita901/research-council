"""OpenAlex adapter — open, CC0, no API key (plan/8). Lazy httpx import."""

from __future__ import annotations

import os

from research_council.store.models import Paper

OPENALEX = "https://api.openalex.org/works"


def _reconstruct_abstract(inv: dict | None) -> str:
    """OpenAlex returns abstracts as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


class OpenAlexProvider:
    name = "openalex"

    def __init__(self, mailto: str | None = None):
        # Polite-pool email (optional); set RC_OPENALEX_MAILTO to enable.
        self.mailto = mailto or os.getenv("RC_OPENALEX_MAILTO")

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        import httpx

        params: dict = {"search": query, "per-page": min(k, 25)}
        if self.mailto:
            params["mailto"] = self.mailto
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(OPENALEX, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception:
            return []  # a dead source degrades the panel, never crashes the run
        out: list[Paper] = []
        for w in data.get("results", [])[:k]:
            wid = w.get("id") or ""
            out.append(Paper(
                id=wid.rsplit("/", 1)[-1] or wid or "(openalex)",
                title=w.get("title") or w.get("display_name") or "(untitled)",
                abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
                year=w.get("publication_year"),
                url=w.get("doi") or wid or None,
                source="openalex",
            ))
        return out
