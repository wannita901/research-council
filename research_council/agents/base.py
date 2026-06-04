"""Peer interface. A peer = one vendor seat; it proposes AND critiques (plan/2 §3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_council.retrieval.base import RetrievalProvider
from research_council.store.models import (
    Candidate,
    Critique,
    Rebuttal,
    ResearchBrief,
    Score,
    VerifierSignal,
)

# An anonymized candidate view passed to critique/score: {label,title,gap,method,experiment_plan}
AnonCandidate = dict


@runtime_checkable
class Peer(Protocol):
    vendor: str

    async def research(self, topic: str, retrieval: RetrievalProvider) -> ResearchBrief: ...
    async def propose(self, brief: ResearchBrief) -> Candidate: ...
    async def critique(self, anon: list[AnonCandidate]) -> list[Critique]: ...
    async def rebut(self, candidate: Candidate, critiques: list[Critique]) -> Rebuttal: ...
    async def score(
        self, anon: list[AnonCandidate], signal_by_label: dict[str, VerifierSignal]
    ) -> list[Score]: ...
