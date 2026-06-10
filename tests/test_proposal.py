"""Stage-A proposal: full proposal fields render, are argued-over (revision), and judged."""

from __future__ import annotations

from research_council.debate.anonymize import anonymize
from research_council.debate.deliberation import _apply_revision
from research_council.store.models import Candidate, CandidateDraft


def _cand(**kw):
    base = dict(id="Aiden", vendor="openai", title="T", gap="g", hypothesis="h", method="m",
                experiment_plan="1. step", problem_statement="prob", motivation="mot",
                dataset_metrics="data·acc", fallback_plan="fb")
    base.update(kw)
    return Candidate(**base)


def test_proposal_md_has_all_sections():
    md = _cand().as_proposal_md()
    for head in ("Problem Statement", "Motivation", "Hypothesis", "Proposed Method",
                 "Step-by-step Experiment Plan", "Dataset / Metrics", "Fallback Plan"):
        assert f"## {head}" in md


def test_numbered_rqs_and_render():
    from research_council.store.models import ResearchQuestion

    # explicit RQs get ids assigned and render under a Research Questions section
    c = _cand(research_questions=[ResearchQuestion(question="q1?", plan="p", metrics="acc"),
                                  ResearchQuestion(question="q2?", plan="p", metrics="f1")])
    rqs = c.numbered_rqs()
    assert [r.id for r in rqs] == ["rq1", "rq2"]
    assert "## Research Questions" in c.as_proposal_md() and "RQ1: q1?" in c.as_proposal_md()


def test_numbered_rqs_falls_back_to_single():
    # no explicit RQs → one RQ synthesized from the overall plan (single-experiment behavior)
    rqs = _cand(research_questions=[]).numbered_rqs()
    assert len(rqs) == 1 and rqs[0].id == "rq1" and rqs[0].plan == "1. step"


def test_revision_patches_any_proposal_field():
    c = _cand()
    _apply_revision(c, CandidateDraft(motivation="stronger motivation",
                                      dataset_metrics="ImageNet·top-1"))
    assert c.motivation == "stronger motivation" and c.dataset_metrics == "ImageNet·top-1"
    assert c.method == "m" and c.version == 2  # untouched fields unchanged, version bumped


def test_anonymized_view_exposes_proposal_for_judging():
    anon, _ = anonymize([_cand()], on=True, seed=1)
    a = anon[0]
    assert a["problem_statement"] == "prob" and a["dataset_metrics"] == "data·acc"
    assert "Aiden" not in a["title"]  # authorship still hidden
