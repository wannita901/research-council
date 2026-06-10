"""Deliberation loop (plan/12 §4b, plan/15 #2) — multi-party, multi-turn discussion.

Round-robin with open-question routing: a peer addressed by an unanswered question
speaks first. Converges when a turn adds no substantive message and no open questions
remain; otherwise stops at `max_turns`. Pseudonymous (peers addressed by codename).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Protocol, runtime_checkable

from research_council.store.models import Candidate, CandidateDraft, Contribution, DiscussionMessage

_SUBSTANTIVE = {"critique", "question", "revise"}


def _apply_revision(c: Candidate, d: CandidateDraft) -> None:
    """Patch a candidate in place with the non-empty fields of a revision draft (any proposal
    field can be argued over and revised, not just the title/plan)."""
    for field in ("title", "problem_statement", "motivation", "hypothesis", "method",
                  "experiment_plan", "dataset_metrics", "fallback_plan"):
        val = getattr(d, field, "")
        if val:
            setattr(c, field, val)
    if getattr(d, "research_questions", None):  # a non-empty list replaces the RQ set
        c.research_questions = d.research_questions
    c.version += 1


@runtime_checkable
class DeliberativePeer(Protocol):
    codename: str

    async def deliberate(
        self, thread: list[DiscussionMessage], candidates: list[Candidate],
        my_open_questions: list[str],
    ) -> Contribution:
        ...


def render_view(thread: list[DiscussionMessage], candidates: list[Candidate],
                my_open_questions: list[str], me: str) -> str:
    cands = "\n".join(f"- {c.id}: {c.title} — {c.gap}" for c in candidates) or "(none)"
    convo = "\n".join(
        f"{m.from_codename} [{m.kind}{(' → ' + m.to) if m.to else ''}]: {m.content}" for m in thread
    ) or "(no messages yet)"
    qs = "\n".join(f"- {q}" for q in my_open_questions) or "(none)"
    return (f"You are {me}.\nCandidates:\n{cands}\n\nDiscussion so far:\n{convo}\n\n"
            f"Open questions addressed to you:\n{qs}\n\nContribute ONE message.")


async def run_deliberation(
    peers: dict[str, DeliberativePeer],
    candidates: list[Candidate],
    *,
    round_no: int = 1,
    max_turns: int = 4,
    emit: Callable[[DiscussionMessage], None] | None = None,
    emit_tool: Callable[[str, list[dict]], None] | None = None,
) -> list[DiscussionMessage]:
    thread: list[DiscussionMessage] = []
    open_qs: dict[str, list[str]] = defaultdict(list, {c: [] for c in peers})
    codenames = list(peers)
    by_id = {c.id: c for c in candidates}

    for turn in range(1, max_turns + 1):
        substantive = False
        # peers with open questions answer first, then the rest (round-robin)
        order = [c for c in codenames if open_qs[c]] + [c for c in codenames if not open_qs[c]]
        for c in order:
            contrib = await peers[c].deliberate(thread, candidates, list(open_qs[c]))
            if emit_tool:
                emit_tool(c, getattr(peers[c], "last_tool_calls", []) or [])
            if contrib.kind == "pass" or (contrib.done and not open_qs[c]):
                continue
            msg = DiscussionMessage(round=round_no, turn=turn, from_codename=c, kind=contrib.kind,
                                    to=contrib.to, content=contrib.content, refs=contrib.refs,
                                    targets=contrib.targets)
            thread.append(msg)
            if emit:
                emit(msg)
            if contrib.kind == "question" and contrib.to in open_qs:
                open_qs[contrib.to].append(contrib.content)
            if contrib.kind == "answer":
                open_qs[c] = []  # treated as answering the questions addressed to me
            # a peer may revise only its OWN candidate (v2: candidate id == codename);
            # patch it in place so the revised text is what gets judged this round.
            if contrib.kind == "revise" and contrib.revision is not None:
                tid = contrib.targets or c
                if tid == c and tid in by_id:
                    _apply_revision(by_id[tid], contrib.revision)
            if contrib.kind in _SUBSTANTIVE:
                substantive = True
        if not substantive and not any(open_qs.values()):
            break  # converged
    return thread
