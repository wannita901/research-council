"""Minimal cost/budget governor (plan/7). Metrics/report are computed from the trace."""

from __future__ import annotations

from dataclasses import asdict, dataclass


def usage_of(run_or_result):
    """Extract a RunUsage from a PydanticAI AgentRun (property) or AgentRunResult.

    In pydantic-ai 1.x `.usage` is a property that returns the usage object; calling it
    (the old `.usage()`) is deprecated. We access it as a property and only fall back to
    calling if needed, so no deprecation warning is emitted."""
    u = getattr(run_or_result, "usage", None)
    if u is None:
        return None
    if hasattr(u, "input_tokens") or hasattr(u, "requests"):
        return u  # already the usage object
    return u() if callable(u) else u


@dataclass
class UsageMeter:
    """Running tally of LLM spend for one peer/agent across a run (tokens, calls, $)."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0

    def add(self, *, requests: int = 0, input_tokens: int = 0, output_tokens: int = 0,
            tool_calls: int = 0, cost_usd: float = 0.0) -> None:
        self.requests += requests
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.tool_calls += tool_calls
        self.cost_usd += cost_usd

    def as_dict(self) -> dict:
        d = asdict(self)
        d["cost_usd"] = round(self.cost_usd, 6)
        return d


class Budget:
    def __init__(self, usd_max: float):
        self.usd_max = usd_max
        self.spent = 0.0

    def add(self, cost_usd: float) -> None:
        self.spent += cost_usd

    @property
    def remaining(self) -> float:
        return max(0.0, self.usd_max - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.usd_max
