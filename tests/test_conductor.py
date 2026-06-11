"""Conversational conductor (`council run`) — offline, non-interactive end-to-end.

On a non-TTY the gates auto-proceed ('go') and the stages fall back to stubs, so the whole
ideation→experimentation→writing lifecycle runs in one command and the project completes.
"""

from __future__ import annotations

from typer.testing import CliRunner

from research_council.cli import app
from research_council.lifecycle import ProjectStore, is_complete


def test_run_conductor_completes_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PROJECTS_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # keep traces/ under tmp too

    result = CliRunner().invoke(app, ["run", "--topic", "toy study of X"])
    assert result.exit_code == 0, result.output
    assert "project complete" in result.output

    store = ProjectStore()
    pid = store.list()[0]
    proj = store.load(pid)
    assert is_complete(proj)
    assert proj.stages["experimentation"].summary and proj.stages["writing"].summary

    # Stage A artifact is a full research proposal document, not just a title
    proposal = (tmp_path / pid / "proposal.md").read_text()
    for head in (
        "Problem Statement",
        "Motivation",
        "Hypothesis",
        "Proposed Method",
        "Step-by-step Experiment Plan",
        "Dataset / Metrics",
        "Fallback Plan",
    ):
        assert f"## {head}" in proposal
    assert proj.stages["ideation"].artifacts.get("proposal_path", "").endswith("proposal.md")


def test_run_resume_continues_existing_project(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PROJECTS_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from research_council.lifecycle import new_project, record_result

    store = ProjectStore()
    p = new_project("toy topic", "proj-x", created="t")
    record_result(
        p, "ideation", summary="idea", artifacts={"idea": {"title": "X"}, "experiment_plan": "plan"}
    )
    store.save(p)  # stopped at ideation/awaiting_approval

    result = CliRunner().invoke(app, ["run", "--resume", "proj-x"])
    assert result.exit_code == 0, result.output
    assert "project complete" in result.output and is_complete(store.load("proj-x"))


def test_run_resume_unknown_id_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PROJECTS_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["run", "--resume", "does-not-exist-zzz"])
    assert result.exit_code != 0 and "no project" in result.output


def test_run_resume_from_stage_rewinds_and_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PROJECTS_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from research_council.lifecycle import approve_and_advance, new_project, record_result
    from research_council.store.models import STAGES

    store = ProjectStore()
    p = new_project("toy topic", "proj-z", created="t")
    for s in STAGES:  # drive it to complete
        record_result(p, s, summary=f"{s} done", artifacts={"idea": {"title": "X"}})
        approve_and_advance(p)
    store.save(p)

    # rewind to experimentation and continue forward (offline → stubs → completes again)
    result = CliRunner().invoke(app, ["run", "--resume", "proj-z", "--from", "experimentation"])
    assert result.exit_code == 0, result.output
    assert "from experimentation" in result.output and is_complete(store.load("proj-z"))


def test_run_from_without_resume_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PROJECTS_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["run", "--topic", "x", "--from", "experimentation"])
    assert result.exit_code != 0 and "--from requires --resume" in result.output


def test_ideation_redo_context_seeds_prior_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PROJECTS_DIR", str(tmp_path))
    from research_council.cli import _ideation_redo_context
    from research_council.lifecycle import new_project, record_result

    p = new_project("t", "proj-y", created="t")
    record_result(
        p,
        "ideation",
        summary="x",
        artifacts={
            "idea": {
                "id": "Aiden",
                "vendor": "openai",
                "title": "Prior Idea",
                "gap": "g",
                "hypothesis": "h",
                "method": "m",
                "experiment_plan": "e",
            }
        },
    )
    ctx = _ideation_redo_context(p, tty=False)  # tty=False → no prompt
    assert "IMPROVE on it" in ctx and "Prior Idea" in ctx
