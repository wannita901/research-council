"""Retrieval registry + adapters — offline (no network) unit tests."""

from __future__ import annotations

from research_council.retrieval.openalex import _reconstruct_abstract
from research_council.retrieval.registry import build_retrieval, build_stub_retrieval


def test_reconstruct_abstract():
    assert _reconstruct_abstract({"Hello": [0], "world": [1]}) == "Hello world"
    assert _reconstruct_abstract({"b": [1], "a": [0]}) == "a b"
    assert _reconstruct_abstract(None) == ""


def test_build_real_constructs_named_providers():
    # Construction only — no .search(), so no network.
    tools = ["wiki", "openalex", "arxiv", "semanticscholar", "github"]
    r = build_retrieval(tools)
    assert {p.name for p in r.providers} == set(tools)
    assert not any(p.__class__.__name__ == "StubRetrieval" for p in r.providers)


def test_unimplemented_tool_falls_back_to_stub():
    r = build_retrieval(["web"])  # web not yet a real adapter
    assert r.providers[0].__class__.__name__ == "StubRetrieval"


def test_unknown_tool_raises():
    import pytest

    with pytest.raises(ValueError):
        build_retrieval(["nope"])


async def test_stub_retrieval_returns_tagged_papers():
    papers = await build_stub_retrieval(["wiki", "openalex"]).search("x", 5)
    assert papers and {p.source for p in papers} == {"wiki", "openalex"}


async def test_cache_collapses_repeat_and_concurrent_queries():
    import asyncio

    from research_council.retrieval.cache import CachedRetrieval
    from research_council.store.models import Paper

    class Counter:
        name = "counter"
        providers = ["sentinel"]  # for delegation check

        def __init__(self):
            self.calls = 0

        async def search(self, query, k=10):
            self.calls += 1
            await asyncio.sleep(0)  # force a yield so concurrent callers overlap
            return [Paper(id=f"p{self.calls}", title="t", source="counter")]

    inner = Counter()
    cached = CachedRetrieval(inner)

    # two concurrent identical searches → a single underlying call (shared in-flight future)
    r1, r2 = await asyncio.gather(cached.search("same q", 5), cached.search("same q", 5))
    assert inner.calls == 1 and r1 == r2
    # repeated query later → served from cache, still one call
    await cached.search("Same   Q", 5)  # normalized to the same key
    assert inner.calls == 1
    # a different query → a fresh call
    await cached.search("other", 5)
    assert inner.calls == 2
    assert cached.misses == 2 and cached.hits >= 2
    # transparent delegation of undefined attributes
    assert cached.providers == ["sentinel"]
    assert cached.name.startswith("cached(")


async def test_build_retrieval_is_cached_but_transparent():
    r = build_retrieval(["openalex", "arxiv"])
    assert r.__class__.__name__ == "CachedRetrieval"
    assert {p.name for p in r.providers} == {"openalex", "arxiv"}  # delegates to hybrid


async def test_wiki_provider_empty_seed_is_network_free():
    from research_council.retrieval.wiki import WikiProvider

    # Point at a non-existent dir → returns [] without touching the network.
    assert await WikiProvider(root="/nonexistent").search("anything") == []


async def test_wiki_origin_tagging_and_external_only(tmp_path):
    from research_council.librarian.schema import WikiPage, render_page
    from research_council.retrieval.wiki import WikiProvider

    w = tmp_path / "wiki"
    (w / "findings").mkdir(parents=True)
    (w / "findings" / "ext.md").write_text(
        render_page(
            WikiPage(
                type="findings",
                title="External finding on flaky tests",
                origin="external",
                body="flaky tests result",
            )
        )
    )
    (w / "findings" / "int.md").write_text(
        render_page(
            WikiPage(
                type="findings",
                title="Council note on flaky tests",
                origin="internal",
                body="flaky tests synthesis",
            )
        )
    )

    wp = WikiProvider(root=tmp_path)
    all_hits = await wp.search("flaky tests")
    assert {p.origin for p in all_hits} == {"external", "internal"}  # gap-finding sees both
    ext_hits = await wp.search("flaky tests", external_only=True)
    assert (
        all_hits and ext_hits and all(p.origin == "external" for p in ext_hits)
    )  # strict prior-art view

    # the search tool flags internal results so a peer won't treat them as prior art
    from research_council.tools.search import SearchTool

    class _R:
        async def search(self, q, k=8):
            return all_hits

    out = await SearchTool(_R()).run("flaky tests")
    assert "council-internal" in out.content
