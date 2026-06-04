"""Minimal cost/budget governor (plan/7). Metrics/report are computed from the trace."""

from __future__ import annotations


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
