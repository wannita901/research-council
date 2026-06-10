"""Code reviewer agent (Stage B council loop, plan/18).

A reviewer reads the experiment code AND its sandbox run, then emits typed findings
{kind, severity, msg, fix} and an approve/reject verdict. A reviewer MAY attach a short
verification probe (a script) to one finding; the engine runs it in the same sandbox and
records whether it executed — evidence-backed review. The agent only proposes probe code;
it never executes anything itself.
"""

from __future__ import annotations

from pydantic_ai import Agent

from research_council import prompts
from research_council.obs.telemetry import UsageMeter, usage_of
from research_council.store.models import CodeReview


class CodeReviewer:
    def __init__(self, model, *, vendor: str = "", price_model: str | None = None):
        self.vendor = vendor
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(
            model, output_type=CodeReview, system_prompt=prompts.load("experiment/reviewer")
        )

    async def review(self, idea: dict, plan: str, code: str, run) -> CodeReview:
        prompt = (
            f"Idea: {idea.get('title', '')}\nHypothesis: {idea.get('hypothesis', '')}\n"
            f"Method: {idea.get('method', '')}\nExperiment plan: {plan}\n\n"
            f"Sandbox run: ran_ok={getattr(run, 'ok', None)} exit={getattr(run, 'exit_code', None)} "
            f"timed_out={getattr(run, 'timed_out', None)}\n"
            f"stdout (tail):\n{(getattr(run, 'stdout', '') or '')[-700:]}\n"
            f"stderr (tail):\n{(getattr(run, 'stderr', '') or '')[-700:]}\n\n"
            f"Code under review (complete, {len(code)} chars):\n```python\n{code}\n```\n\n"
            "Review the code AND the result. Does the metric actually answer the hypothesis? "
            "The code above is the COMPLETE script that ran (it already executed successfully in "
            "the sandbox) — do not claim it is truncated. Approve only if it is sound and runnable."
        )
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        out = r.output
        out.reviewer_vendor = self.vendor
        return out


def _cost_add(meter: UsageMeter, result, price_model: str | None) -> None:
    u = usage_of(result)
    if u is None:
        return
    from research_council.providers.sdk import _cost

    it, ot = u.input_tokens or 0, u.output_tokens or 0
    meter.add(
        requests=u.requests or 0,
        input_tokens=it,
        output_tokens=ot,
        cost_usd=_cost(price_model, it, ot) if price_model else 0.0,
    )
