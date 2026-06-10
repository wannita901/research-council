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
