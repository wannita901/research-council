"""v2 ideation orchestrator — end-to-end offline with deterministic StubV2Peer."""

from __future__ import annotations

from pathlib import Path

from research_council.agents.stub_agent_peer import StubV2Peer
from research_council.debate.orchestrator_v2 import CODENAMES, aggregate_v2, run_ideation
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import DEFAULT_WEIGHTS, Candidate, ReviewAction, Score


def _peers():
    return {cn: StubV2Peer(v, cn) for v, cn in CODENAMES.items()}


async def test_ideation_autonomous_end_to_end(tmp_path: Path):
    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    rec, candidates = await run_ideation("LLM code review vs SAST", _peers(), trace, max_turns=3)
    assert len(candidates) == 3
    assert len(rec.ranked) == 3 and set(rec.ranked) == {c.id for c in candidates}
    text = trace.path.read_text()
    for kind in ("research_brief", "candidate", "discussion_message", "candidate_revised", "recommendation"):
        assert f'"kind":"{kind}"' in text
    # one candidate revised its own plan during deliberation → version bumped + text changed
    revised = [c for c in candidates if c.version > 1]
    assert len(revised) == 1 and "revised toy experiment plan" in revised[0].experiment_plan


async def test_ideation_amend_then_select_reenters(tmp_path: Path):
    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    calls = {"n": 0}

    async def reviewer(rec, candidates, rnd):
        calls["n"] += 1
        if calls["n"] == 1:
            return ReviewAction(action="amend", feedback="focus on security bugs")
        return ReviewAction(action="select", choice=rec.ranked[0])

    rec, _ = await run_ideation("t", _peers(), trace, reviewer=reviewer, max_turns=2)
    text = trace.path.read_text()
    assert calls["n"] == 2  # amend forced a second round
    assert '"kind":"final_choice"' in text
    assert text.count('"kind":"research_brief"') >= 6  # research ran in both rounds


async def test_usage_summary_emitted_for_metered_peers(tmp_path: Path):
    import pytest

    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.agent_peer import AgentPeer
    from research_council.retrieval.registry import build_stub_retrieval

    r = build_stub_retrieval(["wiki"])
    peers = {cn: AgentPeer(v, cn, TestModel(), r, price_model="gpt-5.4")
             for v, cn in CODENAMES.items()}
    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    await run_ideation("t", peers, trace, max_turns=1)
    text = trace.path.read_text()
    assert '"kind":"usage_summary"' in text
    import json
    ev = [json.loads(l) for l in text.splitlines()]
    summ = next(e for e in ev if e["kind"] == "usage_summary")
    assert set(summ["payload"]["by"]) >= set(CODENAMES.values())  # one row per peer
    assert summ["payload"]["totals"]["requests"] > 0
    # tool calls are logged with the calling peer's codename
    tcs = [e for e in ev if e["kind"] == "tool_call"]
    assert tcs and all("codename" in e["payload"] and "tool" in e["payload"] for e in tcs)


async def test_iterate_continues_until_human_concludes(tmp_path: Path):
    # iterate keeps going (bounded only by the hard safety ceiling) until the human concludes
    calls = {"n": 0}

    async def reviewer(rec, candidates, rnd):
        calls["n"] += 1
        return ReviewAction(action="iterate") if calls["n"] < 3 else ReviewAction(action="conclude")

    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    await run_ideation("t", _peers(), trace, reviewer=reviewer, max_turns=1)
    assert calls["n"] == 3  # iterate ran 3 rounds; nothing cut it off early
    assert trace.path.read_text().count('"kind":"research_brief"') >= 9  # 3 rounds × 3 peers


async def test_auto_iterate_drives_autonomous_rounds(tmp_path: Path):
    # no reviewer → the auto-policy runs `auto_rounds` rounds, then concludes
    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    await run_ideation("t", _peers(), trace, auto_rounds=3, max_turns=1)
    assert trace.path.read_text().count('"kind":"research_brief"') == 9  # exactly 3 rounds × 3 peers

    trace1 = TraceWriter.new("ideation", runs_dir=tmp_path)
    await run_ideation("t", _peers(), trace1, max_turns=1)  # default auto_rounds=1
    assert trace1.path.read_text().count('"kind":"research_brief"') == 3  # single round


async def test_safety_ceiling_caps_runaway_iterate(tmp_path: Path):
    # a reviewer that always iterates is stopped at the hard ceiling, emitting a capped event
    async def reviewer(rec, candidates, rnd):
        return ReviewAction(action="iterate")

    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    await run_ideation("t", _peers(), trace, reviewer=reviewer, max_turns=1)
    assert '"kind":"capped"' in trace.path.read_text()


def test_aggregate_excludes_self_scores():
    cands = [
        Candidate(id="Aiden", vendor="openai", title="A", gap="", hypothesis="", method="", experiment_plan=""),
        Candidate(id="Cathy", vendor="anthropic", title="B", gap="", hypothesis="", method="", experiment_plan=""),
    ]
    id_map = {"C1": cands[0], "C2": cands[1]}  # C1=Aiden(openai), C2=Cathy(anthropic)

    def sc(judge, label, v):
        return Score(judge_vendor=judge, candidate_id=label, novelty=v, soundness=v, feasibility=v, clarity=v)

    # each judge inflates its OWN candidate (1.0) and tanks the rival (0.0)
    scores = [sc("openai", "C1", 1.0), sc("openai", "C2", 0.0),
              sc("anthropic", "C2", 1.0), sc("anthropic", "C1", 0.0)]

    rec = aggregate_v2(scores, dict(DEFAULT_WEIGHTS), id_map)            # drop_self (default)
    assert rec.composites["Aiden"] == 0.0 and rec.composites["Cathy"] == 0.0  # only rival's 0.0 counts
    assert "Aiden" in rec.breakdown and set(rec.breakdown["Aiden"]) == set(DEFAULT_WEIGHTS)

    keep = aggregate_v2(scores, dict(DEFAULT_WEIGHTS), id_map, drop_self=False)
    assert keep.composites["Aiden"] == 0.5 and keep.composites["Cathy"] == 0.5  # self+rival averaged


async def test_intake_runs_when_facilitator_present(tmp_path: Path):
    from research_council.store.models import Constraints, IntakeQuestion

    class _Fac:
        async def questions(self, stage, topic):
            return [IntakeQuestion(question="What is success?")]

    async def answer_fn(q):
        return "a clear baseline win"

    trace = TraceWriter.new("ideation", runs_dir=tmp_path)
    rec, _ = await run_ideation("t", _peers(), trace, facilitator=_Fac(), answer_fn=answer_fn,
                                max_turns=2)
    text = trace.path.read_text()
    assert '"kind":"constraints"' in text and "a clear baseline win" in text
