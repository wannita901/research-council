"""`verify_claim` grounding tool (plan/15 #6) — retrieval-backed existence check.

Grounds a disputed claim by checking whether supporting literature/benchmark/repo
exists. The heavy "run a scaffold/tests" verification is a separate Stage-B tool.
"""

from __future__ import annotations

from research_council.retrieval.base import RetrievalProvider
from research_council.tools.base import ToolResult

KINDS = ("citation", "benchmark", "repo", "existence")


class VerifyTool:
    name = "verify_claim"
    description = (
        "Ground a claim by checking whether supporting sources exist. "
        "kind ∈ {citation, benchmark, repo, existence}. Returns grounded/confidence/evidence."
    )

    def __init__(self, retrieval: RetrievalProvider, k: int = 5):
        self.retrieval = retrieval
        self.k = k

    async def run(self, claim: str, kind: str = "existence") -> ToolResult:
        papers = await self.retrieval.search(claim, self.k)
        grounded = len(papers) > 0
        confidence = round(min(1.0, len(papers) / 3), 2)
        refs = [p.id for p in papers[:3]]
        note = f"kind={kind}; {'found' if grounded else 'no'} supporting sources"
        return ToolResult(
            content=f"grounded={grounded} confidence={confidence} evidence={refs} :: {note}",
            refs=refs,
        )
