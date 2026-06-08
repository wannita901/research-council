"""Live peer: drives a real LLMProvider with per-phase prompts + JSON parsing.

Parsing is defensive: models return imperfect JSON (e.g. a structured object where
we asked for a string, or a missing key). We coerce/skip rather than crash the run.
Offline runs use StubPeer instead; this is exercised with `--live`.
"""

from __future__ import annotations

import json

from research_council import prompts
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

# v1 system prompts live in research_council/prompts/v1_*.md (JSON-shaped; loaded verbatim).
RESEARCH_SYS = prompts.load("peer_v1/research")
PROPOSE_SYS = prompts.load("peer_v1/propose")
CRITIQUE_SYS = prompts.load("peer_v1/critique")
SCORE_SYS = prompts.load("peer_v1/score")


def _json(text: str) -> dict:
    """Extract the outermost JSON object; return {} on failure (degrade, don't crash)."""
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)  # object/array → compact string


def _list_str(v) -> list[str]:
    if isinstance(v, list):
        return [x if isinstance(x, str) else _text(x) for x in v]
    return [] if v in (None, "") else [_text(v)]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class LLMPeer:
    def __init__(self, vendor: str, provider: LLMProvider):
        self.vendor = vendor
        self.provider = provider

    async def research(self, topic: str, retrieval: RetrievalProvider) -> ResearchBrief:
        papers = await retrieval.search(topic, k=15)
        listing = "\n".join(f"{p.id} · {p.title} :: {p.abstract[:200]}" for p in papers)
        r = await self.provider.complete(RESEARCH_SYS, f"Topic: {topic}\nPapers:\n{listing}", kind="research")
        d = _json(r.text)
        return ResearchBrief(vendor=self.vendor, landscape=_text(d.get("landscape")),
                             gap=_text(d.get("gap")), rationale=_text(d.get("rationale")),
                             refs=_list_str(d.get("refs")))

    async def propose(self, brief: ResearchBrief) -> Candidate:
        r = await self.provider.complete(PROPOSE_SYS, f"Gap: {brief.gap}\nLandscape: {brief.landscape}", kind="propose")
        d = _json(r.text)
        return Candidate(id=self.vendor, vendor=self.vendor, title=_text(d.get("title")),
                         gap=brief.gap, hypothesis=_text(d.get("hypothesis")),
                         method=_text(d.get("method")), experiment_plan=_text(d.get("experiment_plan")),
                         refs=brief.refs)

    async def critique(self, anon: list[dict]) -> list[Critique]:
        out: list[Critique] = []
        for i in _json(await self._text(CRITIQUE_SYS, json.dumps(anon), "critique")).get("items", []):
            if not isinstance(i, dict) or "label" not in i:
                continue  # skip items we can't attribute to a candidate
            sev = int(_num(i.get("severity", 1))) or 1
            out.append(Critique(critic_vendor=self.vendor, target_id=str(i["label"]),
                                axis=str(i.get("axis", "soundness")), severity=max(1, min(5, sev)),
                                claim=_text(i.get("claim")),
                                needs_verification=bool(i.get("needs_verification", False))))
        return out

    async def rebut(self, candidate: Candidate, critiques: list[Critique]) -> Rebuttal:
        return Rebuttal(candidate_id=candidate.id, notes=f"{len(critiques)} critiques considered")

    async def score(self, anon: list[dict], signal_by_label: dict[str, VerifierSignal]) -> list[Score]:
        sigs = {k: v.feasibility for k, v in signal_by_label.items()}
        payload = json.dumps({"candidates": anon, "verifier": sigs})
        out: list[Score] = []
        for i in _json(await self._text(SCORE_SYS, payload, "score")).get("items", []):
            if not isinstance(i, dict) or "label" not in i:
                continue
            out.append(Score(judge_vendor=self.vendor, candidate_id=str(i["label"]),
                             novelty=_num(i.get("novelty")), soundness=_num(i.get("soundness")),
                             feasibility=_num(i.get("feasibility")), clarity=_num(i.get("clarity"))))
        return out

    async def _text(self, system: str, user: str, kind: str) -> str:
        return (await self.provider.complete(system, user, kind=kind)).text
