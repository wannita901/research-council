"""Round memory (plan/15 #3) — build a structured RoundDigest from a finished round and
render it as the context the next round's research consumes (+ the human's comment verbatim).

Per-debate working memory: the trace holds full detail; this is the compact carry-forward.
"""

from __future__ import annotations

from research_council.store.models import Candidate, DiscussionMessage, ResearchBrief, RoundDigest


def build_round_digest(
    round_no: int,
    briefs: list[ResearchBrief],
    candidates: list[Candidate],
    thread: list[DiscussionMessage],
    *,
    human_comment: str = "",
    codename_of: dict[str, str] | None = None,
    max_critiques: int = 6,
) -> RoundDigest:
    cn = codename_of or {}
    gaps = [f"{cn.get(b.vendor, b.vendor)}: {b.gap}" for b in briefs if b.gap]
    cands = [f"{c.id}: {c.title}" for c in candidates]
    crits = [
        f"{m.from_codename} → {m.targets or 'all'}: {m.content}"
        for m in thread
        if m.kind == "critique"
    ][:max_critiques]
    grounding = [
        f"{m.from_codename}: {m.content}"
        for m in thread
        if m.refs and m.kind in ("answer", "defend")
    ][:4]
    return RoundDigest(
        round=round_no,
        gaps=gaps,
        candidates=cands,
        top_critiques=crits,
        verifier=grounding,
        human_comment=human_comment,
    )


def render_digest(d: RoundDigest) -> str:
    parts = [f"Round {d.round} recap:"]
    if d.gaps:
        parts.append("Gaps so far:\n" + "\n".join(f"- {g}" for g in d.gaps))
    if d.candidates:
        parts.append("Candidates:\n" + "\n".join(f"- {c}" for c in d.candidates))
    if d.top_critiques:
        parts.append("Key critiques:\n" + "\n".join(f"- {c}" for c in d.top_critiques))
    if d.verifier:
        parts.append("Grounding:\n" + "\n".join(f"- {v}" for v in d.verifier))
    if d.human_comment:
        parts.append(f'Human comment (address this):\n"{d.human_comment}"')
    return "\n\n".join(parts)
