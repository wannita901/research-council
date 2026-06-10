"""LaTeX fixer agent (Stage C, plan/22) — repairs a .tex that failed to compile.

Given the source and the compiler's error log, it returns a corrected full .tex document. This
is the LLM half of the build-verify-fix loop: the mechanical pass (escaping, doc-class fallback)
handles the common cases; this agent handles the rest, with the build log as evidence.
"""

from __future__ import annotations

from pydantic_ai import Agent

from research_council import prompts
from research_council.obs.telemetry import UsageMeter, usage_of


class LatexFixer:
    def __init__(self, model, *, price_model: str | None = None):
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(model, output_type=str,
                                   system_prompt=prompts.load("latex/fixer"))

    async def fix(self, tex: str, log: str) -> str:
        prompt = (f"The LaTeX document below failed to compile.\n\n"
                  f"Compiler error log (tail):\n{(log or '')[-1500:]}\n\n"
                  f"Current paper.tex:\n{tex}\n\n"
                  "Return the COMPLETE corrected .tex document (no commentary, no code fences).")
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        out = (r.output or "").strip()
        # strip an accidental ```latex fence if the model added one
        if out.startswith("```"):
            out = out.split("\n", 1)[-1]
            if out.endswith("```"):
                out = out.rsplit("```", 1)[0]
        return out.strip() or tex


def _cost_add(meter: UsageMeter, result, price_model: str | None) -> None:
    u = usage_of(result)
    if u is None:
        return
    from research_council.providers.sdk import _cost
    it, ot = u.input_tokens or 0, u.output_tokens or 0
    meter.add(requests=u.requests or 0, input_tokens=it, output_tokens=ot,
              cost_usd=_cost(price_model, it, ot) if price_model else 0.0)
