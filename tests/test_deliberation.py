"""Deliberation loop — routing/convergence (scripted, deterministic) + agent smoke (TestModel)."""

from __future__ import annotations

import pytest

from research_council.debate.deliberation import DeliberativePeer, run_deliberation
from research_council.store.models import (
    Candidate,
    CandidateDraft,
    Contribution,
    DiscussionMessage,
)

CANDS = [
    Candidate(
        id="C1",
        vendor="openai",
        title="A",
        gap="g1",
        hypothesis="h",
        method="m",
        experiment_plan="e",
    ),
    Candidate(
        id="C2",
        vendor="anthropic",
        title="B",
        gap="g2",
        hypothesis="h",
        method="m",
        experiment_plan="e",
    ),
]


class ScriptedPeer:
    def __init__(self, codename: str, script: list[Contribution]):
        self.codename = codename
        self._script = iter(script)

    async def deliberate(
        self, thread, candidates, my_open_questions, *, require_critique=False
    ) -> Contribution:
        if require_critique:  # mandatory opening critique (not drawn from the free-form script)
            tgt = next(
                (c.id for c in candidates if c.id != self.codename),
                candidates[0].id if candidates else self.codename,
            )
            return Contribution(kind="critique", targets=tgt, content=f"@{tgt} opening concern")
        try:
            return next(self._script)
        except StopIteration:
            return Contribution(kind="pass", done=True)


async def test_question_routing_and_convergence():
    peers: dict[str, DeliberativePeer] = {
        "Aiden": ScriptedPeer(
            "Aiden",
            [Contribution(kind="question", to="Cathy", content="How does it scale?", targets="C2")],
        ),
        "Cathy": ScriptedPeer(
            "Cathy", [Contribution(kind="answer", to="Aiden", content="Via batching.")]
        ),
        "Julien": ScriptedPeer("Julien", []),
    }
    thread = await run_deliberation(peers, CANDS, round_no=1, max_turns=4)

    q = [m for m in thread if m.kind == "question"]
    a = [m for m in thread if m.kind == "answer"]
    assert q and q[0].from_codename == "Aiden" and q[0].to == "Cathy"
    assert a and a[0].from_codename == "Cathy"
    assert all(isinstance(m, DiscussionMessage) for m in thread)
    assert max((m.turn for m in thread), default=0) <= 4  # respected the cap


async def test_opening_guarantees_every_peer_speaks():
    # even if everyone would pass in free-form, the mandatory opening gives each peer one voice
    peers = {c: ScriptedPeer(c, []) for c in ("Aiden", "Cathy", "Julien")}
    thread = await run_deliberation(peers, CANDS, max_turns=4)
    openers = {m.from_codename for m in thread if m.turn == 0}
    assert openers == {"Aiden", "Cathy", "Julien"}  # all three heard
    assert all(m.kind == "critique" for m in thread if m.turn == 0)
    assert len(thread) == 3  # then free-form converges (all pass)


async def test_per_peer_cap_limits_one_loud_peer():
    # a peer that always wants to critique is capped per round (no domination)
    loud = [Contribution(kind="critique", targets="C1", content="again") for _ in range(10)]
    peers: dict[str, DeliberativePeer] = {
        "Aiden": ScriptedPeer("Aiden", loud),
        "Cathy": ScriptedPeer("Cathy", []),
        "Julien": ScriptedPeer("Julien", []),
    }
    thread = await run_deliberation(peers, CANDS, max_turns=8, max_msgs_per_peer=3)
    assert (
        sum(1 for m in thread if m.from_codename == "Aiden") == 3
    )  # opening + 2 free-form, then muted


async def test_revise_patches_own_candidate_in_place():
    cands = [
        Candidate(
            id="Aiden",
            vendor="openai",
            title="A",
            gap="g1",
            hypothesis="h",
            method="m",
            experiment_plan="old plan",
        ),
        Candidate(
            id="Cathy",
            vendor="anthropic",
            title="B",
            gap="g2",
            hypothesis="h",
            method="m",
            experiment_plan="e",
        ),
    ]
    peers = {
        "Aiden": ScriptedPeer(
            "Aiden",
            [
                Contribution(
                    kind="revise",
                    targets="Aiden",
                    content="tightened",
                    revision=CandidateDraft(experiment_plan="new plan", method="new method"),
                )
            ],
        ),
        "Cathy": ScriptedPeer("Cathy", []),
    }
    await run_deliberation(peers, cands, max_turns=2)
    assert cands[0].experiment_plan == "new plan" and cands[0].method == "new method"
    assert cands[0].version == 2  # bumped
    assert cands[0].title == "A"  # untouched fields preserved
    assert cands[1].version == 1  # other candidate unchanged


async def test_revise_cannot_touch_another_peers_candidate():
    cands = [
        Candidate(
            id="Aiden",
            vendor="openai",
            title="A",
            gap="g",
            hypothesis="h",
            method="m",
            experiment_plan="keep",
        )
    ]
    peers = {
        "Cathy": ScriptedPeer(
            "Cathy",
            [
                Contribution(
                    kind="revise",
                    targets="Aiden",
                    content="hostile rewrite",
                    revision=CandidateDraft(experiment_plan="HIJACKED"),
                )
            ],
        ),
        "Aiden": ScriptedPeer("Aiden", []),
    }
    await run_deliberation(peers, cands, max_turns=2)
    assert cands[0].experiment_plan == "keep" and cands[0].version == 1  # Cathy can't edit Aiden's


async def test_deliberation_with_agent_testmodel():
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.agent_peer import AgentPeer
    from research_council.retrieval.registry import build_stub_retrieval

    r = build_stub_retrieval(["wiki"])
    peers = {
        cn: AgentPeer(vendor=v, codename=cn, model=TestModel(), retrieval=r)
        for v, cn in [("openai", "Aiden"), ("anthropic", "Cathy"), ("gemini", "Julien")]
    }
    thread = await run_deliberation(peers, CANDS, max_turns=2)
    assert all(isinstance(m, DiscussionMessage) for m in thread)
