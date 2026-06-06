"""Intake (plan/15 #5) — a cheap facilitator asks stage-specific clarifying questions
to set constraints before the council works. Runs at project start and each stage.

`run_intake` is runner-agnostic: it takes an async `answer_fn` (CLI prompt / backend
endpoint / auto-skip), so it's testable offline and reusable across runners.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from pydantic_ai import Agent

from research_council.store.models import Constraints, IntakeQuestion, IntakeQuestions

FACILITATOR_SYS = (
    "You are the facilitator for an AI4SE research council. Given a stage and topic, ask a few "
    "concise, high-value clarifying questions that set the constraints the council needs before "
    "working. Tailor them to the stage — e.g. ideation: research area, success criteria, known "
    "prior work, scope/compute limits; writing: target venue, page limit, deadline, authors, "
    "emphasis. Ask only what materially changes the work."
)


class Facilitator:
    def __init__(self, model, max_questions: int = 5):
        self.max_questions = max_questions
        self._agent: Agent = Agent(model, output_type=IntakeQuestions, system_prompt=FACILITATOR_SYS)

    async def questions(self, stage: str, topic: str) -> list[IntakeQuestion]:
        prompt = f"Stage: {stage}\nTopic: {topic}\nAsk up to {self.max_questions} clarifying questions."
        result = await self._agent.run(prompt)
        return result.output.questions[: self.max_questions]


async def run_intake(
    facilitator: Facilitator,
    stage: str,
    topic: str,
    answer_fn: Callable[[IntakeQuestion], Awaitable[str]],
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
