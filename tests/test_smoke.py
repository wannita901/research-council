"""Smoke test: the full 7-phase debate runs offline and produces a ranking + trace."""

from __future__ import annotations

from pathlib import Path

from research_council.agents.stub_peer import StubPeer
from research_council.debate.orchestrator import run_debate
from research_council.retrieval.registry import build_stub_retrieval
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import RunConfig
from research_council.verify.mock import MockVerifier


async def test_debate_runs_offline(tmp_path: Path):
    cfg = RunConfig(n_rounds=2, tools=["wiki", "openalex"])
    peers = [StubPeer(v) for v in cfg.seats]
    trace = TraceWriter.new(cfg.stage, runs_dir=tmp_path)

    rec, candidates = await run_debate(
        cfg, "test topic", peers, build_stub_retrieval(cfg.tools), MockVerifier(), trace
    )

    assert len(candidates) == 3
    assert len(rec.ranked) == 3
    assert set(rec.ranked) == {c.id for c in candidates}
    assert trace.path.exists()
    assert trace.path.read_text().count("\n") > 10  # many phase events logged


async def test_review_gate_revise_then_accept(tmp_path: Path):
    from research_council.store.models import ReviewAction

    cfg = RunConfig(n_rounds=1, tools=["wiki"])
    peers = [StubPeer(v) for v in cfg.seats]
    trace = TraceWriter.new(cfg.stage, runs_dir=tmp_path)
    calls = {"n": 0}

    async def reviewer(rec, candidates, rnd):
        calls["n"] += 1
        if calls["n"] == 1:
            return ReviewAction(action="amend", feedback="add a baseline comparison")
        return ReviewAction(action="select", choice=rec.ranked[0])

    rec, _ = await run_debate(
        cfg, "t", peers, build_stub_retrieval(cfg.tools), MockVerifier(), trace, reviewer=reviewer
    )
    text = trace.path.read_text()
    assert calls["n"] == 2  # human "amend" forced a second round past n_rounds=1
    assert '"critic_vendor":"human"' in text  # feedback injected as a human critique
    assert '"kind":"final_choice"' in text


async def test_anonymize_off_uses_real_ids():
    from research_council.debate.anonymize import anonymize
    from research_council.store.models import Candidate

    cands = [
        Candidate(
            id="openai",
            vendor="openai",
            title="t",
            gap="g",
            hypothesis="h",
            method="m",
            experiment_plan="e",
        )
    ]
    anon, id_map = anonymize(cands, on=False)
    assert anon[0]["label"] == "openai"
    assert id_map["openai"].vendor == "openai"
