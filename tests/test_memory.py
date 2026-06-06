"""Round memory — build + render the cross-round digest (offline)."""

from __future__ import annotations

from research_council.debate.memory import build_round_digest, render_digest
from research_council.store.models import Candidate, DiscussionMessage, ResearchBrief, RoundDigest

BRIEFS = [
    ResearchBrief(vendor="openai", landscape="L", gap="gap-A"),
    ResearchBrief(vendor="anthropic", landscape="L", gap="gap-C"),
]
CANDS = [Candidate(id="C1", vendor="openai", title="Idea1", gap="gap-A",
                   hypothesis="h", method="m", experiment_plan="e")]
THREAD = [
    DiscussionMessage(from_codename="Aiden", kind="critique", targets="C1", content="too broad"),
    DiscussionMessage(from_codename="Cathy", kind="answer", content="grounded", refs=["x"]),
]


def test_build_round_digest_attributes_by_codename():
    d = build_round_digest(1, BRIEFS, CANDS, THREAD, human_comment="focus on security",
                           codename_of={"openai": "Aiden", "anthropic": "Cathy"})
    assert "Aiden: gap-A" in d.gaps and "Cathy: gap-C" in d.gaps
    assert "C1: Idea1" in d.candidates
    assert any("too broad" in c for c in d.top_critiques)
    assert d.verifier and d.human_comment == "focus on security"


def test_render_digest_includes_comment_and_critiques():
    d = build_round_digest(1, BRIEFS, CANDS, THREAD, human_comment="focus on security",
                           codename_of={"openai": "Aiden", "anthropic": "Cathy"})
    text = render_digest(d)
    assert "Round 1 recap" in text and "focus on security" in text and "too broad" in text


def test_render_digest_omits_empty_sections():
    text = render_digest(RoundDigest(round=2, gaps=["Aiden: g"], candidates=["C1: t"]))
    assert "Round 2" in text and "Human comment" not in text and "Key critiques" not in text
