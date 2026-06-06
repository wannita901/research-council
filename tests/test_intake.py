"""Intake — answer collection (scripted, deterministic) + facilitator smoke (TestModel)."""

from __future__ import annotations

import pytest

from research_council.agents.facilitator import render_constraints, run_intake
from research_council.store.models import Constraints, IntakeQuestion


class _FakeFacilitator:
    def __init__(self, questions):
        self._questions = questions

    async def questions(self, stage, topic):
        return list(self._questions)


async def test_run_intake_collects_answers():
    fac = _FakeFacilitator([
        IntakeQuestion(question="What is the research area?"),
        IntakeQuestion(question="What counts as success?"),
        IntakeQuestion(question="(skip me)"),
    ])

    async def answer_fn(q: IntakeQuestion) -> str:
        return "" if q.question.startswith("(skip") else f"answer to {q.question}"

    c = await run_intake(fac, "ideation", "LLM code review", answer_fn)
    assert isinstance(c, Constraints) and c.stage == "ideation"
    assert len(c.answers) == 2  # the skipped one is dropped
    assert c.answers["What is the research area?"] == "answer to What is the research area?"


def test_render_constraints():
    c = Constraints(stage="writing", answers={"Target venue?": "ICSE 2027", "Page limit?": "10"})
    text = render_constraints(c)
    assert "writing stage" in text and "ICSE 2027" in text
    assert render_constraints(Constraints(stage="ideation")) == ""  # no answers → empty


async def test_facilitator_questions_offline():
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel
    from research_council.agents.facilitator import Facilitator

    qs = await Facilitator(TestModel(), max_questions=5).questions("ideation", "topic")
    assert isinstance(qs, list) and len(qs) <= 5
