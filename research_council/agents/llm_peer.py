"""Live peer: drives a real LLMProvider with per-phase prompts + JSON parsing.

Wired for completeness; exercised only with `--live` once an SDK adapter is
implemented (providers/sdk.py). Offline runs use StubPeer instead.
"""

from __future__ import annotations

import json

from research_council.providers.base import LLMProvider
from research_council.retrieval.base import RetrievalProvider
from research_council.store.models import (
    Candidate,
    Critique,
    Rebuttal,
    ResearchBrief,
    Score,
    VerifierSignal,
)

RESEARCH_SYS = (
    "You are an AI4SE research scientist. Survey ONLY the provided papers and identify "
    "ONE specific, underexplored gap. Ground claims in provided reference ids; never "
    'invent citations. Return JSON {"landscape","gap","rationale","refs":[ids]}.'
)
PROPOSE_SYS = (
    "Turn your gap into a concrete, testable idea AND a MINIMAL experiment plan runnable "
    "at toy scale (name dataset, baseline, method, metric, smallest runnable step). "
    'Return JSON {"title","hypothesis","method","experiment_plan"}.'
)
CRITIQUE_SYS = (
    "Review anonymized AI4SE candidates on novelty/soundness/feasibility. If a claim is "
    "checkable set needs_verification=true. Return JSON {\"items\":[{label,axis,severity,"
    "claim,needs_verification}]}."
)
SCORE_SYS = (
    "Score each anonymized candidate 0..1 on novelty/soundness/feasibility/clarity. The "
    "feasibility score MUST match the provided verifier signal. Return JSON "
    '{"items":[{label,novelty,soundness,feasibility,clarity}]}.'
)


def _json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


class LLMPeer:
    def __init__(self, vendor: str, provider: LLMProvider):
        self.vendor = vendor
        self.provider = provider

    async def research(self, topic: str, retrieval: RetrievalProvider) -> ResearchBrief:
        papers = await retrieval.search(topic, k=15)
        listing = "\n".join(f"{p.id} · {p.title} :: {p.abstract[:200]}" for p in papers)
        r = await self.provider.complete(RESEARCH_SYS, f"Topic: {topic}\nPapers:\n{listing}", kind="research")
        d = _json(r.text)
        return ResearchBrief(vendor=self.vendor, landscape=d.get("landscape", ""),
                             gap=d.get("gap", ""), rationale=d.get("rationale", ""),
                             refs=d.get("refs", []))

    async def propose(self, brief: ResearchBrief) -> Candidate:
        r = await self.provider.complete(PROPOSE_SYS, f"Gap: {brief.gap}\nLandscape: {brief.landscape}", kind="propose")
        d = _json(r.text)
        return Candidate(id=self.vendor, vendor=self.vendor, title=d.get("title", ""),
                         gap=brief.gap, hypothesis=d.get("hypothesis", ""),
                         method=d.get("method", ""), experiment_plan=d.get("experiment_plan", ""),
                         refs=brief.refs)

    async def critique(self, anon: list[dict]) -> list[Critique]:
        r = await self.provider.complete(CRITIQUE_SYS, json.dumps(anon), kind="critique")
        items = _json(r.text).get("items", [])
        return [Critique(critic_vendor=self.vendor, target_id=i["label"], axis=i.get("axis", "soundness"),
                         severity=int(i.get("severity", 1)), claim=i.get("claim", ""),
                         needs_verification=bool(i.get("needs_verification", False))) for i in items]

    async def rebut(self, candidate: Candidate, critiques: list[Critique]) -> Rebuttal:
        return Rebuttal(candidate_id=candidate.id, notes=f"{len(critiques)} critiques considered")

    async def score(self, anon: list[dict], signal_by_label: dict[str, VerifierSignal]) -> list[Score]:
        sigs = {k: v.feasibility for k, v in signal_by_label.items()}
        r = await self.provider.complete(SCORE_SYS, json.dumps({"candidates": anon, "verifier": sigs}), kind="score")
        items = _json(r.text).get("items", [])
        return [Score(judge_vendor=self.vendor, candidate_id=i["label"], novelty=float(i.get("novelty", 0)),
                      soundness=float(i.get("soundness", 0)), feasibility=float(i.get("feasibility", 0)),
                      clarity=float(i.get("clarity", 0))) for i in items]
