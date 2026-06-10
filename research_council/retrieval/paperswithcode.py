"""Papers With Code adapter (plan/8). Links papers ↔ code ↔ benchmarks.

NOT currently registered in retrieval/registry.py: the public API is Cloudflare/anti-bot
gated and returns the site's HTML to non-browser clients (→ degrades to []). Kept here so
it can be re-enabled if a reachable endpoint/credential becomes available.
"""

from __future__ import annotations

from research_council.store.models import Paper

PWC = "https://paperswithcode.com/api/v1/papers/"


class PapersWithCodeProvider:
    name = "paperswithcode"

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        import httpx

        params = {"q": query, "items_per_page": min(k, 50)}
        headers = {"User-Agent": "research-council/0.0.1", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers) as c:
                r = await c.get(PWC, params=params)
                r.raise_for_status()
                data = r.json()  # HTML/anti-bot responses fail here → degrade to []
        except Exception:
            return []
        out: list[Paper] = []
        for w in (data.get("results") or [])[:k]:
            pub = (w.get("published") or "")[:4]
            out.append(
                Paper(
                    id=str(w.get("id") or w.get("arxiv_id") or "(paperswithcode)"),
                    title=w.get("title") or "(untitled)",
                    abstract=w.get("abstract") or "",
                    year=int(pub) if pub.isdigit() else None,
                    url=w.get("url_abs"),
                    source="paperswithcode",
                )
            )
        return out
