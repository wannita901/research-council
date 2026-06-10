"""Cap profiles for the Stage B/C council loops (plan/18).

Three presets bound tokens/time; whichever of {iteration cap, USD budget} binds first
stops the loop and returns best-so-far. All values are overridable per-run from the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageBCaps:
    max_iters: int
    k: int                 # approvals required (of 2 reviewers)
    usd_budget: float
    timeout: int           # sandbox seconds per run
    probe_timeout: int = 15


@dataclass(frozen=True)
class StageCCaps:
    max_revisions: int
    accept: float          # mean rubric score to accept
    usd_budget: float
    block_severities: tuple[str, ...] = ("high",)   # change-request severities that block accept
    latex_fix_attempts: int = 3


STAGE_B_PROFILES = {
    "conservative": StageBCaps(max_iters=2, k=1, usd_budget=0.60, timeout=30),
    "balanced":     StageBCaps(max_iters=3, k=2, usd_budget=1.50, timeout=30),
    "thorough":     StageBCaps(max_iters=5, k=2, usd_budget=4.00, timeout=60),
}

STAGE_C_PROFILES = {
    "conservative": StageCCaps(max_revisions=2, accept=0.65, usd_budget=0.60),
    "balanced":     StageCCaps(max_revisions=3, accept=0.70, usd_budget=1.50),
    "thorough":     StageCCaps(max_revisions=5, accept=0.78, usd_budget=4.00,
                               block_severities=("high", "medium")),
}

DEFAULT_PROFILE = "balanced"


def stage_b_caps(profile: str = DEFAULT_PROFILE) -> StageBCaps:
    return STAGE_B_PROFILES.get(profile, STAGE_B_PROFILES[DEFAULT_PROFILE])


def stage_c_caps(profile: str = DEFAULT_PROFILE) -> StageCCaps:
    return STAGE_C_PROFILES.get(profile, STAGE_C_PROFILES[DEFAULT_PROFILE])


def total_spend(*agents) -> float:
    """Sum cost_usd across any agents exposing a `.usage` UsageMeter (offline → 0.0)."""
    total = 0.0
    for a in agents:
        m = getattr(a, "usage", None)
        if m is not None:
            total += getattr(m, "cost_usd", 0.0)
    return round(total, 6)
