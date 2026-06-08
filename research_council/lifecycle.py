"""Macro lifecycle (plan/13; Tier 2 #8 + #9) — the A→B→C state machine.

A Project moves through three human-gated stages: ideation → experimentation → writing.
Each stage runs its council-mode, then waits for human approval before advancing; a
StageHandoff carries the selected idea + plan + constraints forward (cross-stage memory).

Stage A (ideation) runs for real (debate/orchestrator_v2). Stages B and C are stubs here
(Tier 3) — they record the handoff and report "not yet implemented", so the whole
lifecycle is already walkable end-to-end. The state machine + store below are pure and
offline-testable; persistence is one JSON file per project under projects/<id>/.
"""

from __future__ import annotations

import os
from pathlib import Path

from research_council.store.models import STAGES, Project, StageHandoff, StageState


def new_project(topic: str, project_id: str, *, created: str = "", constraints: dict | None = None) -> Project:
    stages = {n: StageState(name=n, status=("active" if n == STAGES[0] else "pending")) for n in STAGES}
    return Project(id=project_id, topic=topic, created=created, current=STAGES[0],
                   constraints=constraints or {}, stages=stages,
                   log=[f"created · {STAGES[0]} active"])


def next_stage(stage: str) -> str | None:
    i = STAGES.index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def record_result(project: Project, stage: str, *, run_id: str | None = None,
                  summary: str = "", artifacts: dict | None = None) -> Project:
    """A stage finished running → park it at awaiting_approval with its outputs."""
    s = project.stages[stage]
    s.run_id, s.summary, s.artifacts, s.status = run_id, summary, (artifacts or {}), "awaiting_approval"
    project.log.append(f"{stage} → awaiting_approval")
    return project


def build_handoff(project: Project, from_stage: str) -> StageHandoff | None:
    to = next_stage(from_stage)
    if to is None:
        return None
    a = project.stages[from_stage].artifacts
    return StageHandoff(
        from_stage=from_stage, to_stage=to,
        idea=a.get("idea", {}), experiment_plan=a.get("experiment_plan", ""),
        constraints=project.constraints, notes=project.stages[from_stage].summary, artifacts=a,
    )


def approve_and_advance(project: Project) -> tuple[Project, StageHandoff | None]:
    """Human approves the current stage → it becomes approved; advance to the next (active).
    Returns the handoff into the next stage (None if the project is complete)."""
    cur = project.current
    if project.stages[cur].status != "awaiting_approval":
        raise ValueError(f"stage {cur!r} is {project.stages[cur].status}, not awaiting_approval")
    project.stages[cur].status = "approved"
    handoff = build_handoff(project, cur)
    nxt = next_stage(cur)
    if nxt is not None:
        project.current = nxt
        project.stages[nxt].status = "active"
        project.log.append(f"approved {cur} → {nxt} active")
    else:
        project.log.append(f"approved {cur} · project complete")
    return project, handoff


def is_complete(project: Project) -> bool:
    return all(s.status == "approved" for s in project.stages.values())


def run_stage_stub(stage: str, handoff: StageHandoff) -> tuple[str, dict]:
    """Stages B and C are not implemented yet (Tier 3). Carry the handoff forward and
    report what each WOULD do, so the lifecycle is walkable. Returns (summary, artifacts)."""
    idea = handoff.idea.get("title", "(the selected idea)")
    base = {"idea": handoff.idea, "experiment_plan": handoff.experiment_plan,
            "status": "not_implemented", "from_handoff": handoff.model_dump()}
    if stage == "experimentation":
        summary = (f"[stub] would implement and run '{idea}' (plan: "
                   f"{handoff.experiment_plan[:80] or 'n/a'}) in a Docker sandbox, then verify the artifacts.")
    elif stage == "writing":
        summary = "[stub] would scaffold-paper from the results, draft sections, and reviewer-critique vs the venue rubric."
    else:
        summary = ""
    return summary, base


class ProjectStore:
    """One JSON file per project under projects/<id>/project.json."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.getenv("RC_PROJECTS_DIR", "projects"))

    def _path(self, pid: str) -> Path:
        return self.root / pid / "project.json"

    def save(self, project: Project) -> Path:
        p = self._path(project.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        return p

    def load(self, pid: str) -> Project:
        return Project.model_validate_json(self._path(pid).read_text(encoding="utf-8"))

    def exists(self, pid: str) -> bool:
        return self._path(pid).exists()

    def list(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(d.name for d in self.root.iterdir() if (d / "project.json").exists())
