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
                         gap=brief.gap, hypothesis="stub hypothesis", method="stub method",
                         experiment_plan="toy experiment plan", refs=brief.refs)

    async def deliberate(self, thread: list[DiscussionMessage], candidates: list[Candidate],
                         my_open_questions: list[str]) -> Contribution:
        # `thread` is fresh each round, so "have I already spoken?" resets per round.
        if any(m.from_codename == self.codename for m in thread) or not candidates:
            return Contribution(kind="pass", done=True)
        first = candidates[0]
        if first.id == self.codename:
            # author of the first candidate revises its own plan during the discussion
            return Contribution(
                kind="revise", targets=self.codename,
                content=f"[{self.codename}] tightened the experiment plan",
                revision=CandidateDraft(experiment_plan="revised toy experiment plan (v2)"),
            )
        return Contribution(kind="critique", targets=first.id,
                            content=f"[{self.codename}] concern about {first.id}")

    async def score(self, anon_candidates: list[dict], context: str = "") -> list[Score]:
        out: list[Score] = []
        for a in anon_candidates:
            base = _unit(self.codename, a["label"])
            out.append(Score(judge_vendor=self.vendor, candidate_id=a["label"], novelty=base,
                             soundness=round(base - 0.05, 2), feasibility=base, clarity=base))
        return out
