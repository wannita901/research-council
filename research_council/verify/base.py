"""Verifier abstraction — the tie-breaker (plan/2 §5)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_council.store.models import Candidate, VerifierSignal


@runtime_checkable
class Verifier(Protocol):
    mode: str

    async def verify(self, candidate: Candidate) -> VerifierSignal: ...
