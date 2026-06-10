"""The debate orchestrator — the 7-phase loop (plan/2 §4, plan/4, plan/6).

Runner-agnostic: it writes every phase event to the trace and optionally streams
each Event to an `emit` callback (so a future UI just subscribes to that stream).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from research_council.agents.base import Peer
from research_council.debate.anonymize import anonymize
from research_council.debate.termination import should_continue
from research_council.retrieval.base import RetrievalProvider
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import (
    Candidate,
    Critique,
    Event,
    Recommendation,
    ReviewAction,
    RunConfig,
    Score,
    VerifierSignal,
)
from research_council.verify.base import Verifier

# Hard ceiling so human "revise" loops can't run away (cost + safety).
SAFETY_MAX_ROUNDS = 8

Reviewer = Callable[[Recommendation, list[Candidate], int], Awaitable[ReviewAction]]


async def _auto_review(rec: Recommendation, candidates: list[Candidate], rnd: int) -> ReviewAction:
    """Default reviewer: no human in the loop — defers to autonomous termination."""
    return ReviewAction(action="auto")


def _aggregate(
    scores: list[Score],
    signal_by_label: dict[str, VerifierSignal],
    weights: dict[str, float],
    id_map: dict[str, Candidate],
) -> Recommendation:
    by_label: dict[str, list[Score]] = defaultdict(list)
    for s in scores:
        by_label[s.candidate_id].append(s)
    composites: dict[str, float] = {}
    for label, slist in by_label.items():
        n = len(slist)
        nov = sum(s.novelty for s in slist) / n
        snd = sum(s.soundness for s in slist) / n
        cla = sum(s.clarity for s in slist) / n
        sig = signal_by_label.get(label)
        feas = sig.feasibility if sig else sum(s.feasibility for s in slist) / n
        comp = (
            weights["novelty"] * nov
            + weights["soundness"] * snd
            + weights["feasibility"] * feas
            + weights["clarity"] * cla
        )
        composites[id_map[label].id] = round(comp, 4)
    ranked = sorted(composites, key=composites.get, reverse=True)
    return Recommendation(
        ranked=ranked,
        composites=composites,
        rationale="anonymized panel vote; feasibility from verifier",
    )


async def run_debate(
    cfg: RunConfig,
    topic: str,
    peers: list[Peer],
    retrieval: RetrievalProvider,
    verifier: Verifier,
    trace: TraceWriter,
    emit: Callable[[Event], None] | None = None,
    reviewer: Reviewer | None = None,
) -> tuple[Recommendation, list[Candidate]]:
    by_vendor = {p.vendor: p for p in peers}
    review = reviewer or _auto_review

    def out(phase: str, kind: str, payload: dict, **kw) -> None:
        ev = trace.emit(phase, kind, payload, **kw)
        if emit:
            emit(ev)

    out("onboarding", "topic", {"topic": topic, "config": cfg.model_dump()})

    # Phase 1 — independent research, in parallel.
    briefs = await asyncio.gather(*(p.research(topic, retrieval) for p in peers))
    for b in briefs:
        out("research", "research_brief", b.model_dump(), author_vendor=b.vendor)

    # Phase 2 — propose.
    candidates: list[Candidate] = list(
        await asyncio.gather(*(p.propose(b) for p, b in zip(peers, briefs, strict=False)))
    )
    for c in candidates:
        out("propose", "candidate", c.model_dump(), author_vendor=c.vendor)

    rec: Recommendation | None = None
    pending_feedback: tuple[str, str | None] | None = None  # (text, target candidate id)
    final_choice: str | None = None
    rnd = 0
    while True:
        rnd += 1
        anon, id_map = anonymize(candidates, cfg.anonymize)
        label_of = {c.id: label for label, c in id_map.items()}

        # Phase 3 — cross-critique (anonymized).
        crit_lists = await asyncio.gather(*(p.critique(anon) for p in peers))
        critiques = [c for lst in crit_lists for c in lst]

        # Inject the human's prior-round feedback as a high-severity critique.
        if pending_feedback is not None:
            text, target = pending_feedback
            targets = [target] if target else [c.id for c in candidates]
            for cid in targets:
                critiques.append(
                    Critique(
                        critic_vendor="human",
                        target_id=label_of[cid],
                        axis="feasibility",
                        severity=4,
                        claim=text,
                    )
                )
            pending_feedback = None

        for c in critiques:
            out(
                "cross_critique",
                "critique",
                c.model_dump(),
                round=rnd,
                author_vendor=c.critic_vendor,
            )

        # Phase 4 — rebut / revise.
        for c in candidates:
            mine = [x for x in critiques if x.target_id == label_of[c.id]]
            r = await by_vendor[c.vendor].rebut(c, mine)
            out("rebut", "rebuttal", r.model_dump(), round=rnd, author_vendor=c.vendor)

        # Phase 5 — verify (ground-truth tie-breaker).
        signals = await asyncio.gather(*(verifier.verify(c) for c in candidates))
        signal_by_label = {label_of[s.candidate_id]: s for s in signals}
        for s in signals:
            out("verify", "verifier_signal", s.model_dump(), round=rnd)

        # Phase 6 — judge (anonymized panel vote, verifier-weighted).
        score_lists = await asyncio.gather(*(p.score(anon, signal_by_label) for p in peers))
        scores = [s for lst in score_lists for s in lst]
        for s in scores:
            out("judge", "score", s.model_dump(), round=rnd, author_vendor=s.judge_vendor)

        rec = _aggregate(scores, signal_by_label, cfg.weights, id_map)
        out("judge", "recommendation", rec.model_dump(), round=rnd)

        # Human review gate (plan/11). Default reviewer just proceeds.
        action = await review(rec, candidates, rnd)
        out("review", "human_action", action.model_dump(), round=rnd, author_vendor="human")

        if action.action == "select":
            final_choice = action.choice or (rec.ranked[0] if rec.ranked else None)
            break
        if action.action == "conclude":
            break
        if rnd >= SAFETY_MAX_ROUNDS:
            break  # ceiling guards iterate/amend loops
        if action.action == "amend":
            pending_feedback = (action.feedback, action.choice)
            continue
        if action.action == "iterate":
            continue
        # "auto" (default reviewer): honour the autonomous termination policy.
        if not should_continue(rnd, cfg.n_rounds, critiques):
            break

    if final_choice:
        out(
            "judge",
            "final_choice",
            {"candidate_id": final_choice},
            round=rnd,
            author_vendor="human",
        )

    assert rec is not None
    return rec, candidates
