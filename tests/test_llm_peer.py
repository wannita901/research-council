"""LLMPeer parses imperfect model JSON without crashing (regression for the live error)."""

from __future__ import annotations

from research_council.agents.llm_peer import LLMPeer
from research_council.providers.base import Response
from research_council.store.models import ResearchBrief


class _FakeProvider:
    """Returns a canned JSON string regardless of prompt."""

    name = "openai"
    model = "fake"

    def __init__(self, text: str):
        self._text = text

    async def complete(self, system: str, user: str, *, kind: str = "") -> Response:
        return Response(text=self._text)


async def test_propose_coerces_object_experiment_plan():
    # The actual live failure: experiment_plan came back as an object, not a string.
    text = (
        '{"title":"T","hypothesis":"H","method":"M",'
        '"experiment_plan":{"dataset":"D","baseline":"B","metric":"m"}}'
    )
    cand = await LLMPeer("openai", _FakeProvider(text)).propose(
        ResearchBrief(vendor="openai", landscape="L", gap="G")
    )
    assert isinstance(cand.experiment_plan, str)
    assert "dataset" in cand.experiment_plan


async def test_critique_skips_unlabeled_and_coerces_severity():
    text = (
        '{"items":[{"axis":"soundness","severity":"3","claim":{"x":1}},'
        '{"label":"C2","severity":2,"claim":"ok"}]}'
    )
    crits = await LLMPeer("openai", _FakeProvider(text)).critique(
        [{"label": "C1"}, {"label": "C2"}]
    )
    assert len(crits) == 1  # the label-less item is skipped
    assert crits[0].target_id == "C2" and crits[0].severity == 2


async def test_score_coerces_numeric_strings():
    text = (
        '{"items":[{"label":"C1","novelty":"0.7","soundness":0.6,"feasibility":0.8,"clarity":0.5}]}'
    )
    scores = await LLMPeer("openai", _FakeProvider(text)).score([{"label": "C1"}], {})
    assert scores[0].novelty == 0.7


async def test_malformed_json_degrades_to_empty():
    cand = await LLMPeer("openai", _FakeProvider("sorry, no JSON here")).propose(
        ResearchBrief(vendor="openai", landscape="L", gap="G")
    )
    assert cand.experiment_plan == "" and cand.title == ""  # no crash
