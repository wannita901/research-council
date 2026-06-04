"""Termination rules (plan/2 §4). Default: consensus or max rounds."""

from __future__ import annotations

from research_council.store.models import Critique


def should_continue(round_no: int, n_rounds: int, critiques: list[Critique]) -> bool:
    if round_no >= n_rounds:
        return False
    # Consensus: stop once no substantive (severity >= 3) objection remains.
    return any(c.severity >= 3 for c in critiques)
