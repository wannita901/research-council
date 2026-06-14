"""Cap profiles for the Stage B/C council loops (plan/18).

Three presets bound tokens/time; whichever of {iteration cap, USD budget} binds first
stops the loop and returns best-so-far. All values are overridable per-run from the CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class StageBCaps:
    max_iters: int
    k: int  # approvals required (of 2 reviewers)
    usd_budget: float
    timeout: int  # sandbox seconds per run
    probe_timeout: int = 15


@dataclass(frozen=True)
class StageCCaps:
    max_revisions: int
    accept: float  # mean rubric score to accept
    usd_budget: float
    block_severities: tuple[str, ...] = ("high",)  # change-request severities that block accept
    latex_fix_attempts: int = 3
    # plan/25 Gap 1: when True, an unbacked numeric claim (no matching value in results.csv)
    # blocks acceptance, forcing the writer to cite/back/remove it within the revision cap.
    # Default False = flag-not-block (claims still drive revision when the loop runs for other
    # reasons, and always land in claims.json). `thorough` turns the teeth on.
    claims_unbacked_block: bool = False


STAGE_B_PROFILES = {
    "conservative": StageBCaps(max_iters=2, k=1, usd_budget=0.60, timeout=30),
    "balanced": StageBCaps(max_iters=3, k=2, usd_budget=1.50, timeout=30),
    "thorough": StageBCaps(max_iters=5, k=2, usd_budget=4.00, timeout=60),
}

# Stage A · ideation caps per profile (balanced == the historical RunConfig defaults).
STAGE_A_PROFILES = {
    "conservative": {
        "max_iters": 3,
        "max_tool_calls": 5,
        "max_turns": 2,
        "max_rounds": 2,
        "max_msgs_per_peer": 2,
        "usd_max": 2.0,
    },
    "balanced": {
        "max_iters": 5,
        "max_tool_calls": 8,
        "max_turns": 4,
        "max_rounds": 4,
        "max_msgs_per_peer": 3,
        "usd_max": 5.0,
    },
    "thorough": {
        "max_iters": 8,
        "max_tool_calls": 12,
        "max_turns": 6,
        "max_rounds": 6,
        "max_msgs_per_peer": 4,
        "usd_max": 12.0,
    },
}

# Stage A per-field env overrides (field → env var); int unless noted.
_STAGE_A_ENV = {
    "max_iters": "RC_MAX_ITERS",
    "max_tool_calls": "RC_MAX_TOOL_CALLS",
    "max_turns": "RC_MAX_TURNS",
    "max_rounds": "RC_MAX_ROUNDS",
    "max_msgs_per_peer": "RC_MAX_MSGS_PER_PEER",
}

STAGE_C_PROFILES = {
    "conservative": StageCCaps(max_revisions=2, accept=0.65, usd_budget=0.60),
    "balanced": StageCCaps(max_revisions=3, accept=0.70, usd_budget=1.50),
    "thorough": StageCCaps(
        max_revisions=5,
        accept=0.78,
        usd_budget=4.00,
        block_severities=("high", "medium"),
        claims_unbacked_block=True,
    ),
}

DEFAULT_PROFILE = "balanced"


def resolve_profile(profile: str | None = None) -> str:
    """CLI flag > RC_PROFILE env (mise) > balanced — resolved at call time."""
    return profile or os.getenv("RC_PROFILE") or DEFAULT_PROFILE


def _i(name: str, cur: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v not in (None, "") else cur
    except ValueError:
        return cur


def _f(name: str, cur: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v not in (None, "") else cur
    except ValueError:
        return cur


def _b(name: str, cur: bool) -> bool:
    v = os.getenv(name)
    if v in (None, ""):
        return cur
    return v.strip().lower() in ("1", "true", "yes", "on")


def stage_a_caps(profile: str | None = None) -> dict:
    """Stage-A (ideation) caps from the profile preset, then per-field env overrides
    (RC_MAX_*). Returned as a dict to overlay onto RunConfig. Precedence: env > profile > default."""
    base = dict(STAGE_A_PROFILES.get(resolve_profile(profile), STAGE_A_PROFILES["balanced"]))
    for field, env in _STAGE_A_ENV.items():
        base[field] = _i(env, base[field])
    base["usd_max"] = _f("RC_USD_MAX", base["usd_max"])
    return base


def stage_b_caps(profile: str | None = None) -> StageBCaps:
    """Profile preset, then per-field env overrides (RC_STAGEB_*) so any cap is tunable in mise."""
    c = STAGE_B_PROFILES.get(resolve_profile(profile), STAGE_B_PROFILES["balanced"])
    return replace(
        c,
        max_iters=_i("RC_STAGEB_MAX_ITERS", c.max_iters),
        k=_i("RC_STAGEB_K", c.k),
        usd_budget=_f("RC_STAGEB_USD", c.usd_budget),
        timeout=_i("RC_STAGEB_TIMEOUT", c.timeout),
        probe_timeout=_i("RC_STAGEB_PROBE_TIMEOUT", c.probe_timeout),
    )


def stage_c_caps(profile: str | None = None) -> StageCCaps:
    """Profile preset, then per-field env overrides (RC_STAGEC_*) so any cap is tunable in mise."""
    c = STAGE_C_PROFILES.get(resolve_profile(profile), STAGE_C_PROFILES["balanced"])
    return replace(
        c,
        max_revisions=_i("RC_STAGEC_MAX_REVISIONS", c.max_revisions),
        accept=_f("RC_STAGEC_ACCEPT", c.accept),
        usd_budget=_f("RC_STAGEC_USD", c.usd_budget),
        latex_fix_attempts=_i("RC_STAGEC_LATEX_FIX_ATTEMPTS", c.latex_fix_attempts),
        claims_unbacked_block=_b("RC_STAGEC_BLOCK_CLAIMS", c.claims_unbacked_block),
    )


def total_spend(*agents) -> float:
    """Sum cost_usd across any agents exposing a `.usage` UsageMeter (offline → 0.0)."""
    total = 0.0
    for a in agents:
        m = getattr(a, "usage", None)
        if m is not None:
            total += getattr(m, "cost_usd", 0.0)
    return round(total, 6)
