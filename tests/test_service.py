"""FastAPI backend — runs over the same core, offline (stub peers, no network)."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from research_council.service.app import Run, app  # noqa: E402
from research_council.store.checkpoint import TraceWriter  # noqa: E402
from research_council.store.models import Recommendation, ReviewAction, RunConfig  # noqa: E402


def _poll(client, run_id, predicate, budget=4.0):
    deadline = time.time() + budget
    while time.time() < deadline:
        s = client.get(f"/debates/{run_id}").json()
        if predicate(s):
            return s
        time.sleep(0.02)
    return client.get(f"/debates/{run_id}").json()


def test_unknown_run_404():
    assert TestClient(app).get("/debates/nope").status_code == 404


def test_autonomous_run_streams_to_completion():
    # `with` keeps the event-loop portal open so the background debate task runs.
    with TestClient(app) as client:
        rid = client.post("/debates", json={"topic": "t", "rounds": 1, "interactive": False}).json()["run_id"]
        with client.stream("GET", f"/debates/{rid}/stream") as r:  # drains until sentinel
            body = "".join(r.iter_text())
        assert "research_brief" in body and "recommendation" in body
        s = client.get(f"/debates/{rid}").json()
    assert s["status"] == "done"
    assert len(s["recommendation"]["ranked"]) == 3


def test_interactive_review_gate_over_http():
    with TestClient(app) as client:
        rid = client.post("/debates", json={"topic": "t", "rounds": 1, "interactive": True}).json()["run_id"]
        s = _poll(client, rid, lambda s: s["awaiting_review"])
        assert s["awaiting_review"]
        assert client.post(f"/debates/{rid}/action", json={"action": "conclude"}).json()["ok"]
        s = _poll(client, rid, lambda s: s["status"] == "done")
    assert s["status"] == "done"


def _poll_ideation(client, run_id, predicate, budget=5.0):
    deadline = time.time() + budget
    while time.time() < deadline:
        s = client.get(f"/ideations/{run_id}").json()
        if predicate(s):
            return s
        time.sleep(0.02)
    return client.get(f"/ideations/{run_id}").json()


def test_ideation_autonomous_streams_to_completion():
    with TestClient(app) as client:
        rid = client.post("/ideations", json={"topic": "t", "interactive": False}).json()["run_id"]
        with client.stream("GET", f"/ideations/{rid}/stream") as r:
            body = "".join(r.iter_text())
        assert "research_brief" in body and "recommendation" in body
        s = client.get(f"/ideations/{rid}").json()
    assert s["status"] == "done"
    assert len(s["recommendation"]["ranked"]) == 3


def test_ideation_auto_iterate_over_http():
    with TestClient(app) as client:
        rid = client.post("/ideations",
                          json={"topic": "t", "interactive": False, "auto_iterate": 2}).json()["run_id"]
        with client.stream("GET", f"/ideations/{rid}/stream") as r:
            body = "".join(r.iter_text())
        assert body.count("research_brief") >= 6  # 2 rounds × 3 peers
        assert client.get(f"/ideations/{rid}").json()["status"] == "done"


def test_ideation_interactive_review_gate_over_http():
    with TestClient(app) as client:
        rid = client.post("/ideations", json={"topic": "t", "interactive": True}).json()["run_id"]
        s = _poll_ideation(client, rid, lambda s: s["awaiting_review"])
        assert s["awaiting_review"]
        assert client.post(f"/ideations/{rid}/action", json={"action": "conclude"}).json()["ok"]
        s = _poll_ideation(client, rid, lambda s: s["status"] == "done")
    assert s["status"] == "done"


def test_ideation_accepts_preset_constraints():
    with TestClient(app) as client:
        rid = client.post("/ideations", json={
            "topic": "t", "interactive": False,
            "constraints": {"What is success?": "a clear baseline win"},
        }).json()["run_id"]
        with client.stream("GET", f"/ideations/{rid}/stream") as r:
            body = "".join(r.iter_text())
        assert "a clear baseline win" in body  # constraints emitted into the trace/stream


async def test_run_reviewer_future_resolves(tmp_path):
    import asyncio

    run = Run(RunConfig(), "t", TraceWriter.new("ideation", runs_dir=tmp_path))
    rec = Recommendation(ranked=["openai"], composites={"openai": 0.5})
    task = asyncio.create_task(run.reviewer(rec, [], 1))
    await asyncio.sleep(0.01)
    assert run.status == "awaiting_review"
    assert run.submit_action(ReviewAction(action="conclude"))
    assert (await task).action == "conclude"
