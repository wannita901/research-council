"""Macro lifecycle — state machine + persistence + stage stubs (pure, offline)."""

from __future__ import annotations

import pytest

from research_council.lifecycle import (
    ProjectStore,
    approve_and_advance,
    build_handoff,
    is_complete,
    new_project,
    next_stage,
    record_result,
    run_stage_stub,
)
from research_council.store.models import STAGES


def test_new_project_initial_state():
    p = new_project("Can LLMs do research?", "proj-1")
    assert p.current == "ideation"
    assert p.stages["ideation"].status == "active"
    assert p.stages["experimentation"].status == "pending"
    assert p.stages["writing"].status == "pending"


def test_next_stage():
    assert next_stage("ideation") == "experimentation"
    assert next_stage("experimentation") == "writing"
    assert next_stage("writing") is None


def test_full_walk_with_handoffs():
    p = new_project("t", "proj-2")
    # Stage A finished → selected idea recorded
    record_result(p, "ideation", run_id="run-x", summary="HypoSE-Bench",
                  artifacts={"idea": {"title": "HypoSE-Bench", "vendor": "anthropic"},
                             "experiment_plan": "Juliet + CodeQL"})
    assert p.stages["ideation"].status == "awaiting_approval"

    # approve A → B active; handoff carries the idea + plan forward
    p, h1 = approve_and_advance(p)
    assert p.current == "experimentation" and p.stages["ideation"].status == "approved"
    assert h1.from_stage == "ideation" and h1.to_stage == "experimentation"
    assert h1.idea["title"] == "HypoSE-Bench" and h1.experiment_plan == "Juliet + CodeQL"

    # run B (stub) → it carries the idea forward and parks at awaiting_approval
    summary, arts = run_stage_stub("experimentation", h1)
    assert "Docker" in summary and arts["idea"]["title"] == "HypoSE-Bench"
    record_result(p, "experimentation", summary=summary, artifacts=arts)

    # approve B → C active; the idea is still in the handoff (chained through B's artifacts)
    p, h2 = approve_and_advance(p)
    assert p.current == "writing" and h2.idea["title"] == "HypoSE-Bench"
    summary, arts = run_stage_stub("writing", h2)
    assert "scaffold-paper" in summary
    record_result(p, "writing", summary=summary, artifacts=arts)

    # approve C → complete (no further handoff)
    p, h3 = approve_and_advance(p)
    assert h3 is None and is_complete(p)
    assert all(p.stages[s].status == "approved" for s in STAGES)


def test_approve_requires_awaiting_approval():
    p = new_project("t", "proj-3")  # ideation is 'active', not awaiting_approval
    with pytest.raises(ValueError):
        approve_and_advance(p)


def test_build_handoff_none_at_last_stage():
    p = new_project("t", "proj-4")
    assert build_handoff(p, "writing") is None


def test_project_store_roundtrip(tmp_path):
    store = ProjectStore(root=tmp_path)
    p = new_project("topic here", "proj-5", created="2026-06-08")
    record_result(p, "ideation", summary="idea", artifacts={"idea": {"title": "X"}})
    store.save(p)
    assert store.exists("proj-5") and store.list() == ["proj-5"]
    loaded = store.load("proj-5")
    assert loaded.topic == "topic here" and loaded.stages["ideation"].summary == "idea"
    assert loaded.stages["ideation"].artifacts["idea"]["title"] == "X"
