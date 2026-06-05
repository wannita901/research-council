"""GitHub repository-search adapter (plan/8). Token optional (higher rate limits).

Grounds AI4SE approaches in real implementations/benchmarks. Repo search works
unauthenticated (low rate); set GITHUB_TOKEN for more.
"""

from __future__ import annotations

import os

from research_council.store.models import Paper

GH = "https://api.github.com/search/repositories"


class GitHubProvider:
    name = "github"

    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        import httpx

        headers = {"Accept": "application/vnd.github+json", "User-Agent": "research-council/0.0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = {"q": query, "per_page": min(k, 50), "sort": "stars"}
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers) as c:
                r = await c.get(GH, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception:
            return []
        out: list[Paper] = []
        for it in (data.get("items") or [])[:k]:
            pushed = (it.get("pushed_at") or "")[:4]
            out.append(Paper(
                id=it.get("full_name") or "(github)",
                title=it.get("full_name") or "(repo)",
                abstract=it.get("description") or "",
                year=int(pushed) if pushed.isdigit() else None,
                url=it.get("html_url"),
                source="github",
            ))
        return out
