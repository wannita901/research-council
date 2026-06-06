"""Per-debate memoizing retrieval wrapper (plan/10 backlog).

The three peers research in parallel and tend to issue overlapping queries (the
topic itself, common terms) — and a peer often repeats a query across rounds.
`CachedRetrieval` collapses identical `(query, k)` searches into a single
downstream fan-out, including *concurrent* duplicates (it shares one in-flight
future), so a strict public API like arXiv (1 req / 3 s) is hit far less.

Scope is one wrapper instance = one debate: build it per run and discard it, so
nothing leaks across debates. The wrapper is transparent — attributes it doesn't
define (e.g. `.providers`) delegate to the wrapped provider.
"""

from __future__ import annotations

import asyncio

from research_council.store.models import Paper


class CachedRetrieval:
    def __init__(self, inner):
        self._inner = inner
        self.name = f"cached({getattr(inner, 'name', 'retrieval')})"
        self._done: dict[tuple[str, int], list[Paper]] = {}
        self._inflight: dict[tuple[str, int], asyncio.Future] = {}
        self.hits = 0
        self.misses = 0

    def __getattr__(self, item):
        # only reached for attributes not set above — delegate to the wrapped provider
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._inner, item)

    @staticmethod
    def _key(query: str, k: int) -> tuple[str, int]:
        return (" ".join(query.split()).lower(), k)

    async def search(self, query: str, k: int = 10) -> list[Paper]:
        key = self._key(query, k)
        if key in self._done:
            self.hits += 1
            return self._done[key]
        inflight = self._inflight.get(key)
        if inflight is not None:  # an identical search is already running — ride along
            self.hits += 1
            return await inflight

        self.misses += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._inflight[key] = fut
        try:
            res = await self._inner.search(query, k)
        except Exception as exc:
            self._inflight.pop(key, None)
            if not fut.done():
                fut.set_exception(exc)
            raise
        self._done[key] = res
        self._inflight.pop(key, None)
        if not fut.done():
            fut.set_result(res)
        return res
