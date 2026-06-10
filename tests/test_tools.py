"""Tool layer — offline (stub retrieval, no network)."""

from __future__ import annotations

from research_council.retrieval.registry import build_stub_retrieval
from research_council.tools.base import Tool
from research_council.tools.search import SearchTool
from research_council.tools.verify import VerifyTool


async def test_search_tool_returns_results_and_refs():
    tool = SearchTool(build_stub_retrieval(["wiki", "openalex"]), k=5)
    assert isinstance(tool, Tool)
    res = await tool.run(query="LLM code review")
    assert res.refs and "wiki" in res.content


async def test_verify_tool_grounded_when_results():
    res = await VerifyTool(build_stub_retrieval(["openalex"])).run(
        claim="X helps Y", kind="citation"
    )
    assert "grounded=True" in res.content and res.refs


async def test_verify_tool_handles_empty(monkeypatch):
    from research_council.retrieval.hybrid import HybridRetrieval

    empty = HybridRetrieval([])  # no providers → no results
    res = await VerifyTool(empty).run(claim="nothing", kind="existence")
    assert "grounded=False" in res.content and res.refs == []
