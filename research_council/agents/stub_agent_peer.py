"""Deterministic offline v2 peer — runs the whole ideation loop with no API keys.

Mirrors the v1 StubPeer: exercises the v2 flow + contracts (research / propose /
deliberate / score) without an LLM. Used by `council ideate` offline and the e2e test.
"""

from __future__ import annotations

import hashlib

from research_council.store.models import (
    Candidate,
    CandidateDraft,
    Contribution,
    DiscussionMessage,
    ResearchBrief,
    ResearchQuestion,
    Score,
)


def _unit(*parts: str) -> float:
    h = int(hashlib.sha1("|".join(parts).encode()).hexdigest(), 16) % 46
    return round(0.5 + h / 100, 2)  # 0.50 .. 0.95


class StubV2Peer:
    def __init__(self, vendor: str, codename: str, retrieval=None):
        self.vendor = vendor
        self.codename = codename
        self.retrieval = retrieval

    async def research(self, topic: str, context: str = "") -> ResearchBrief:
        return ResearchBrief(
            vendor=self.vendor,
            landscape=f"[{self.codename}] surveyed '{topic}'" + (" (+context)" if context else ""),
            gap=f"[{self.codename}] underexplored angle on '{topic}'.",
            rationale="stub", refs=[],
        )

    async def propose(self, brief: ResearchBrief, constraints_text: str = "") -> Candidate:
        return Candidate(id=self.codename, vendor=self.vendor, title=f"[{self.codename}] idea",
                         gap=brief.gap, problem_statement="stub problem statement",
                         motivation="stub motivation", hypothesis="stub hypothesis",
                         method="stub method", experiment_plan="1. toy step\n2. measure",
                         research_questions=[
                             ResearchQuestion(id="rq1", question="does the toy method work?",
                                              plan="1. run toy\n2. measure", metrics="accuracy"),
                             ResearchQuestion(id="rq2", question="does it beat the baseline?",
                                              plan="1. run baseline\n2. compare", metrics="accuracy"),
                         ],
                         dataset_metrics="synthetic toy data · accuracy",
                         fallback_plan="simplify to a smaller toy run", refs=brief.refs)

    async def deliberate(self, thread: list[DiscussionMessage], candidates: list[Candidate],
                         my_open_questions: list[str], *, require_critique: bool = False) -> Contribution:
        # the opening turn demands a critique of a peer's (non-self) candidate
        if require_critique:
            target = next((c.id for c in candidates if c.id != self.codename),
                          candidates[0].id if candidates else self.codename)
            return Contribution(kind="critique", targets=target,
                                content=f"@{target} [{self.codename}] concern about {target}'s soundness")
        # free-form (after the opening): the first candidate's author revises ONCE; others pass.
        # (keyed on a prior REVISE from me, not "spoke at all", since the opening already spoke.)
        if not candidates:
            return Contribution(kind="pass", done=True)
        already_revised = any(m.from_codename == self.codename and m.kind == "revise" for m in thread)
        if candidates[0].id == self.codename and not already_revised:
            return Contribution(
                kind="revise", targets=self.codename,
                content=f"[{self.codename}] tightened the experiment plan",
                revision=CandidateDraft(experiment_plan="revised toy experiment plan (v2)"),
            )
        return Contribution(kind="pass", done=True)

    async def score(self, anon_candidates: list[dict], context: str = "") -> list[Score]:
        out: list[Score] = []
        for a in anon_candidates:
            base = _unit(self.codename, a["label"])
            out.append(Score(judge_vendor=self.vendor, candidate_id=a["label"], novelty=base,
                             soundness=round(base - 0.05, 2), feasibility=base, clarity=base))
        return out
