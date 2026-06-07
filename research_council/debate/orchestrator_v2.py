"""v2 ideation orchestrator (plan/12 + plan/13 Stage A) — agentic, stitched together.

  intake → ROUND[ research(+constraints/digest) → propose → deliberate → judge(anon) ]
         → human gate → re-enter with RoundDigest + comment

Kept alongside v1 (debate/orchestrator.py). Peers are duck-typed: any object with
research / propose / deliberate / score (AgentPeer for --live, StubV2Peer offline).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

from research_council.agents.facilitator import Facilitator, render_constraints, run_intake
from research_council.debate.anonymize import anonymize
from research_council.debate.deliberation import run_deliberation
from research_council.debate.memory import build_round_digest, render_digest
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import (
    DEFAULT_WEIGHTS,
    Candidate,
    Constraints,
    Event,
    IntakeQuestion,
    Recommendation,
    ReviewAction,
    Score,
)

# Fixed default codename↔vendor mapping (hidden from agents; plan/15 #7).
CODENAMES = {"openai": "Aiden", "anthropic": "Cathy", "gemini": "Julien"}

# Hard ceiling on rounds (human-driven or auto) so nothing can loop forever.
SAFETY_MAX_ROUNDS = 8


def _auto_policy(auto_rounds: int):
    """Default (no-human) reviewer: auto-iterate until `auto_rounds`, then conclude.
    auto_rounds=1 → a single round (the system never continues without approval)."""
    async def review(rec: Recommendation, candidates: list[Candidate], rnd: int) -> ReviewAction:
        return ReviewAction(action="iterate" if rnd < auto_rounds else "conclude")
    return review


_AXES = ("novelty", "soundness", "feasibility", "clarity")


def aggregate_v2(scores: list[Score], weights: dict[str, float], id_map,
                 *, drop_self: bool = True) -> Recommendation:
    """Mean of judges' weighted scores per candidate. Self-scores (a judge scoring its own
    candidate) are excluded by default — the self-preference guard alongside anonymization."""
    by: dict[str, list[Score]] = defaultdict(list)
    for s in scores:
        if s.candidate_id not in id_map:  # ignore labels we don't recognise
            continue
        if drop_self and s.judge_vendor == id_map[s.candidate_id].vendor:
            continue  # a peer doesn't get to score its own idea
        by[s.candidate_id].append(s)
    # fallback: if dropping self left a candidate with no scores, re-include all for it
    for label in id_map:
        if label not in by:
            by[label] = [s for s in scores if s.candidate_id == label]

    composites: dict[str, float] = {}
    breakdown: dict[str, dict] = {}
    for label, sl in by.items():
        cid = id_map[label].id
        if not sl:
            composites[cid] = 0.0
            breakdown[cid] = {a: 0.0 for a in _AXES}
            continue
        n = len(sl)
        ax = {a: round(sum(getattr(s, a) for s in sl) / n, 3) for a in _AXES}
        composites[cid] = round(sum(weights[a] * ax[a] for a in _AXES), 4)
        breakdown[cid] = ax
    ranked = sorted(composites, key=composites.get, reverse=True)
    return Recommendation(ranked=ranked, composites=composites, breakdown=breakdown,
                          rationale="anonymized panel vote · self-scores excluded")


async def run_ideation(
    topic: str,
    peers: dict[str, Any],  # codename -> peer
    trace: TraceWriter,
    *,
    facilitator: Facilitator | None = None,
    answer_fn: Callable[[IntakeQuestion], Awaitable[str]] | None = None,
    reviewer: Callable[[Recommendation, list[Candidate], int], Awaitable[ReviewAction]] | None = None,
    weights: dict[str, float] | None = None,
    auto_rounds: int = 1,   # no-human runs auto-iterate this many rounds, then conclude
    max_turns: int = 4,
    anonymize_on: bool = True,
    emit: Callable[[Event], None] | None = None,
) -> tuple[Recommendation, list[Candidate]]:
    weights = weights or dict(DEFAULT_WEIGHTS)
    review = reviewer or _auto_policy(auto_rounds)
    vendor_to_cn = {p.vendor: cn for cn, p in peers.items()}

    def out(phase: str, kind: str, payload: dict, **kw) -> None:
        ev = trace.emit(phase, kind, payload, **kw)
        if emit:
            emit(ev)

    out("intake", "topic", {"topic": topic, "codenames": list(peers)})

    constraints = Constraints(stage="ideation")
    if facilitator is not None and answer_fn is not None:
        constraints = await run_intake(facilitator, "ideation", topic, answer_fn)
    out("intake", "constraints", constraints.model_dump())
    constraints_text = render_constraints(constraints)

    order = list(peers.items())
    digest_text = ""
    rec: Recommendation | None = None
    candidates: list[Candidate] = []
    final_choice: str | None = None
    rnd = 0
    while True:
        rnd += 1
        ctx = "\n\n".join(x for x in [constraints_text, digest_text] if x)

        def emit_tools(phase: str, cn: str, tcs: list[dict]) -> None:
            for tc in tcs:
                out(phase, "tool_call", {**tc, "codename": cn}, round=rnd)

        briefs = await asyncio.gather(*(p.research(topic, ctx) for _, p in order))
        for (cn, p), b in zip(order, briefs):
            out("research", "research_brief", {**b.model_dump(), "codename": cn},
                round=rnd, author_vendor=b.vendor)
            emit_tools("research", cn, getattr(p, "last_tool_calls", []) or [])

        candidates = list(await asyncio.gather(
            *(p.propose(b, constraints_text) for (_, p), b in zip(order, briefs))))
        for c in candidates:
            out("propose", "candidate", {**c.model_dump(), "codename": vendor_to_cn.get(c.vendor, c.id)},
                round=rnd, author_vendor=c.vendor)

        thread = await run_deliberation(
            peers, candidates, round_no=rnd, max_turns=max_turns,
            emit=lambda m: out("deliberate", "discussion_message", m.model_dump(), round=rnd),
            emit_tool=lambda cn, tcs: emit_tools("deliberate", cn, tcs),
        )
        # candidates revised mid-deliberation (version bumped) are re-emitted before judging
        for c in candidates:
            if c.version > 1:
                out("deliberate", "candidate_revised",
                    {**c.model_dump(), "codename": vendor_to_cn.get(c.vendor, c.id)},
                    round=rnd, author_vendor=c.vendor)

        anon, id_map = anonymize(candidates, anonymize_on, seed=rnd)
        score_lists = await asyncio.gather(*(p.score(anon) for _, p in order))
        scores = [s for sl in score_lists for s in sl]
        for s in scores:
            out("judge", "score", s.model_dump(), round=rnd, author_vendor=s.judge_vendor)
        rec = aggregate_v2(scores, weights, id_map)
        out("judge", "recommendation", rec.model_dump(), round=rnd)

        action = await review(rec, candidates, rnd)
        out("review", "human_action", action.model_dump(), round=rnd, author_vendor="human")
        if action.action == "select":
            final_choice = action.choice or (rec.ranked[0] if rec.ranked else None)
            break
        if action.action == "conclude":
            break
        # iterate / amend (human-driven, or the auto-policy) → another round, bounded only by
        # the hard safety ceiling so nothing can loop forever.
        if rnd >= SAFETY_MAX_ROUNDS:
            out("review", "capped", {"hard_max_rounds": SAFETY_MAX_ROUNDS}, round=rnd)
            break

        comment = action.feedback if action.action == "amend" else ""
        digest = build_round_digest(rnd, list(briefs), candidates, thread,
                                    human_comment=comment, codename_of=vendor_to_cn)
        digest_text = render_digest(digest)

    if final_choice:
        out("judge", "final_choice", {"candidate_id": final_choice}, round=rnd, author_vendor="human")

    # cost/usage summary — peers/facilitator that track usage (offline stubs are skipped)
    by: dict[str, dict] = {}
    for cn, p in peers.items():
        m = getattr(p, "usage", None)
        if m is not None and (m.requests or m.input_tokens or m.output_tokens):
            by[cn] = m.as_dict()
    fac_m = getattr(facilitator, "usage", None) if facilitator is not None else None
    if fac_m is not None and (fac_m.requests or fac_m.input_tokens):
        by["facilitator"] = fac_m.as_dict()
    if by:
        totals: dict[str, float] = {}
        for m in by.values():
            for k, v in m.items():
                totals[k] = round(totals.get(k, 0) + v, 6)
        out("run", "usage_summary", {"by": by, "totals": totals})

    assert rec is not None
    return rec, candidates
