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


async def test_wiki_provider_empty_seed_is_network_free():
    from research_council.retrieval.wiki import WikiProvider

    # Point at a non-existent dir → returns [] without touching the network.
    assert await WikiProvider(root="/nonexistent").search("anything") == []
