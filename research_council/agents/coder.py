"""Coder agent (Stage B, plan/13) — turns an idea + plan into a minimal runnable script.

Pairs with verify/sandbox.py: the council drafts a script, the sandbox runs it, and on
failure the error is fed back for a bounded fix-and-retry. Offline-testable via TestModel.
"""

from __future__ import annotations

from pydantic_ai import Agent

from research_council import prompts
from research_council.obs.telemetry import UsageMeter, usage_of
from research_council.store.models import ExperimentDraft


class Coder:
    def __init__(self, model, *, price_model: str | None = None):
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(model, output_type=ExperimentDraft,
                                   system_prompt=prompts.load("experiment/coder"))

    async def draft(self, idea: dict, plan: str, *, error: str = "") -> ExperimentDraft:
        prompt = (f"Idea: {idea.get('title', '')}\nHypothesis: {idea.get('hypothesis', '')}\n"
                  f"Method: {idea.get('method', '')}\nExperiment plan: {plan}\n\n"
                  "Write the smallest runnable script per the rules.")
        if error:
            prompt += f"\n\nThe previous attempt FAILED with:\n{error[:900]}\nFix the script."
        r = await self._agent.run(prompt)
        self._track(r)
        return r.output

    def _track(self, r) -> None:
        u = usage_of(r)
        if u is None:
            return
        from research_council.providers.sdk import _cost
        it, ot = u.input_tokens or 0, u.output_tokens or 0
        self.usage.add(requests=u.requests or 0, input_tokens=it, output_tokens=ot,
                       cost_usd=_cost(self._price_model, it, ot) if self._price_model else 0.0)
