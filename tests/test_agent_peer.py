"""Agentic research peer — offline via PydanticAI TestModel (no API, no tokens)."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_ai")

from pydantic_ai.models.test import TestModel  # noqa: E402

from research_council.agents.agent_peer import AgentPeer, agent_model_name  # noqa: E402
from research_council.retrieval.registry import build_stub_retrieval  # noqa: E402
from research_council.store.models import ResearchBrief  # noqa: E402


def test_model_name_mapping():
    # non-deprecated PydanticAI v1 prefixes (google replaces google-gla; openai-chat keeps Chat Completions)
    assert agent_model_name("gemini", "gemini-3.5-flash") == "google:gemini-3.5-flash"
    assert agent_model_name("openai", "gpt-5.4") == "openai-chat:gpt-5.4"
    assert agent_model_name("anthropic", "claude-sonnet-4-6") == "anthropic:claude-sonnet-4-6"


async def test_agent_peer_research_runs_offline():
    peer = AgentPeer(
        vendor="openai",
        codename="Aiden",
        model=TestModel(),  # drives the loop deterministically, calls tools, no network
        retrieval=build_stub_retrieval(["wiki", "openalex"]),
        max_iters=4,
        max_tool_calls=6,
    )
    brief = await peer.research("Do LLM code-review agents beat SAST on security bugs?")
    assert isinstance(brief, ResearchBrief)
    assert brief.vendor == "openai"
    assert isinstance(brief.gap, str) and isinstance(brief.refs, list)


async def test_usage_is_tracked_and_costed():
    peer = AgentPeer(
        vendor="openai",
        codename="Aiden",
        model=TestModel(),
        retrieval=build_stub_retrieval(["wiki"]),
        price_model="gpt-5.4",
    )
    await peer.research("does X beat Y?")
    await peer.propose(await peer.research("again"))
    u = peer.usage
    assert u.requests > 0 and u.input_tokens > 0
    assert u.cost_usd > 0  # gpt-5.4 priced in PRICES → non-zero
    # without a price_model, tokens still counted but cost stays 0
    free = AgentPeer("gemini", "Julien", TestModel(), build_stub_retrieval(["wiki"]))
    await free.research("x")
    assert free.usage.requests > 0 and free.usage.cost_usd == 0.0


async def test_tool_calls_are_recorded():
    # TestModel calls every available tool once → research should record search + verify_claim.
    peer = AgentPeer(
        vendor="openai",
        codename="Aiden",
        model=TestModel(),
        retrieval=build_stub_retrieval(["wiki"]),
    )
    await peer.research("does X beat Y?")
    tools = {tc["tool"] for tc in peer.last_tool_calls}
    assert "search" in tools  # actually called the search tool
    assert all("tool" in tc and "args" in tc for tc in peer.last_tool_calls)


async def test_research_finalizes_gracefully_when_tool_budget_hit():
    # tool_calls cap of 0 → the first tool call would exceed it. The peer must NOT crash;
    # it should finalize tool-lessly and still return a ResearchBrief (this is the bug the
    # live run hit: UsageLimitExceeded killed the whole debate).
    peer = AgentPeer(
        vendor="gemini",
        codename="Julien",
        model=TestModel(),
        retrieval=build_stub_retrieval(["wiki"]),
        max_iters=3,
        max_tool_calls=0,
    )
    brief = await peer.research("anything")
    assert isinstance(brief, ResearchBrief) and brief.vendor == "gemini"
