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
        self._agent: Agent = Agent(
            model, output_type=ExperimentDraft, system_prompt=prompts.load("experiment/coder")
        )

    async def draft(
        self, idea: dict, plan: str, *, error: str = "", prior_code: str = "", feedback: str = ""
    ) -> ExperimentDraft:
        prompt = (
            f"Proposal: {idea.get('title', '')}\n"
            f"Problem: {idea.get('problem_statement', '') or idea.get('gap', '')}\n"
            f"Hypothesis: {idea.get('hypothesis', '')}\n"
            f"Method: {idea.get('method', '')}\n"
            f"Experiment plan: {plan or idea.get('experiment_plan', '')}\n"
            f"Dataset/metrics: {idea.get('dataset_metrics', '')}\n"
            f"Fallback plan: {idea.get('fallback_plan', '')}\n\n"
            "Implement the smallest runnable step of the plan per the rules; the METRIC you "
            "print should be one of the proposal's evaluation metrics (or a clear proxy)."
        )
        if prior_code:
            prompt += (
                f"\n\nYour previous script:\n```python\n{prior_code[:2500]}\n```\n"
                "Revise it — keep what works, change only what the feedback requires."
            )
        if error:
            prompt += f"\n\nThe previous run FAILED with:\n{error[:900]}\nFix the cause."
        if feedback:
            prompt += f"\n\nReviewer findings to address:\n{feedback[:1200]}"
        r = await self._agent.run(prompt)
        self._track(r)
        return r.output

    def _track(self, r) -> None:
        u = usage_of(r)
        if u is None:
            return
        from research_council.providers.sdk import _cost

        it, ot = u.input_tokens or 0, u.output_tokens or 0
        self.usage.add(
            requests=u.requests or 0,
            input_tokens=it,
            output_tokens=ot,
            cost_usd=_cost(self._price_model, it, ot) if self._price_model else 0.0,
        )
