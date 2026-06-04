"""Increment-1 feasibility verifier: a deterministic heuristic, no sandbox.

Increment 2 (verify/sandbox.py) will generate a minimal runnable scaffold from
candidate.experiment_plan and execute it in a container (plan/6 §2, increment 2).
"""

from __future__ import annotations

import hashlib

from research_council.store.models import Candidate, VerifierSignal


class MockVerifier:
    mode = "mock"

    async def verify(self, candidate: Candidate) -> VerifierSignal:
        h = int(hashlib.sha1(candidate.id.encode()).hexdigest(), 16) % 100
        feasibility = round(0.4 + (h / 100) * 0.55, 2)  # 0.40 .. 0.95
        return VerifierSignal(
            candidate_id=candidate.id,
            runnable=feasibility >= 0.5,
            feasibility=feasibility,
            log="mock: heuristic feasibility (no execution)",
        )
