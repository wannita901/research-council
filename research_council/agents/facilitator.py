"""Onboarding (plan/15 #5) — a cheap facilitator asks stage-specific clarifying questions
to set constraints before the council works. Runs at project start and each stage.

`run_onboarding` is runner-agnostic: it takes an async `answer_fn` (CLI prompt / backend
endpoint / auto-skip), so it's testable offline and reusable across runners.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic_ai import Agent

from research_council import prompts
from research_council.obs.telemetry import UsageMeter, usage_of
from research_council.store.models import Constraints, OnboardingQuestion, OnboardingQuestions


class Facilitator:
    def __init__(self, model, max_questions: int = 5, price_model: str | None = None):
        self.max_questions = max_questions
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(
            model,
            output_type=OnboardingQuestions,
            system_prompt=prompts.load("facilitator/onboarding"),
        )

    async def questions(self, stage: str, topic: str) -> list[OnboardingQuestion]:
        prompt = (
            f"Stage: {stage}\nTopic: {topic}\nAsk up to {self.max_questions} clarifying questions."
        )
        result = await self._agent.run(prompt)
        u = usage_of(result)
        if u is not None:
            from research_council.providers.sdk import _cost

            it, ot = u.input_tokens or 0, u.output_tokens or 0
            self.usage.add(
                requests=u.requests or 0,
                input_tokens=it,
                output_tokens=ot,
                cost_usd=_cost(self._price_model, it, ot) if self._price_model else 0.0,
            )
        return result.output.questions[: self.max_questions]


async def run_onboarding(
    facilitator: Facilitator,
    stage: str,
    topic: str,
    answer_fn: Callable[[OnboardingQuestion], Awaitable[str]],
) -> Constraints:
    """Generate questions, collect human answers via `answer_fn`, return Constraints."""
    questions = await facilitator.questions(stage, topic)
    answers: dict[str, str] = {}
    for i, q in enumerate(questions, 1):
        q.id = q.id or f"q{i}"
        ans = (await answer_fn(q) or "").strip()
        if ans:
            answers[q.question] = ans
    return Constraints(stage=stage, answers=answers)


def render_constraints(c: Constraints) -> str:
    if not c.answers:
        return ""
    body = "\n".join(f"- {q}: {a}" for q, a in c.answers.items())
    return f"Constraints for the {c.stage} stage:\n{body}"
