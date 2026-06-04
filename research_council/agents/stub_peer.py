"""Deterministic offline peer — runs the whole debate with no API keys.

Content varies by vendor + topic so diversity/score aggregation is non-trivial.
"""

from __future__ import annotations

import hashlib

from research_council.retrieval.base import RetrievalProvider
from research_council.store.models import (
    Candidate,
    Critique,
    Rebuttal,
    ResearchBrief,
    Score,
    VerifierSignal,
)


def _unit(*parts: str) -> float:
    """Deterministic float in [0.5, 0.95] from the given parts."""
    h = int(hashlib.sha1("|".join(parts).encode()).hexdigest(), 16) % 46
    return round(0.5 + h / 100, 2)


class StubPeer:
    def __init__(self, vendor: str):
        self.vendor = vendor

    async def research(self, topic: str, retrieval: RetrievalProvider) -> ResearchBrief:
        papers = await retrieval.search(topic, k=5)
        return ResearchBrief(
            vendor=self.vendor,
            landscape=f"[{self.vendor}] surveyed {len(papers)} sources on '{topic}'.",
            gap=f"[{self.vendor}] underexplored angle on '{topic}'.",
            rationale="stub rationale",
            refs=[p.id for p in papers[:3]],
        )

    async def propose(self, brief: ResearchBrief) -> Candidate:
        return Candidate(
            id=self.vendor,
            vendor=self.vendor,
            title=f"[{self.vendor}] {brief.gap[:48]}",
            gap=brief.gap,
            hypothesis=f"[{self.vendor}] hypothesis",
            method=f"[{self.vendor}] method sketch",
            experiment_plan=f"toy experiment proposed by {self.vendor}",
            refs=brief.refs,
        )

    async def critique(self, anon: list[dict]) -> list[Critique]:
        return [
            Critique(
                critic_vendor=self.vendor,
                target_id=a["label"],
                axis="soundness",
                severity=2,
                claim=f"[{self.vendor}] concern about {a['label']}",
                needs_verification=False,
            )
            for a in anon
        ]

    async def rebut(self, candidate: Candidate, critiques: list[Critique]) -> Rebuttal:
        return Rebuttal(
            candidate_id=candidate.id,
            notes=f"[{self.vendor}] addressed {len(critiques)} critiques",
            revised=False,
        )

    async def score(
        self, anon: list[dict], signal_by_label: dict[str, VerifierSignal]
    ) -> list[Score]:
        out: list[Score] = []
        for a in anon:
            base = _unit(self.vendor, a["label"])
            sig = signal_by_label.get(a["label"])
            feas = sig.feasibility if sig else base
            out.append(
                Score(
                    judge_vendor=self.vendor,
                    candidate_id=a["label"],
                    novelty=base,
                    soundness=round(base - 0.05, 2),
                    feasibility=feas,
                    clarity=base,
                    rationale="stub score",
                )
            )
        return out
