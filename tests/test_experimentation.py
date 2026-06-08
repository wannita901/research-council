"""Stage B — sandbox + experimentation loop (offline; LocalSandbox runs controlled scripts)."""

from __future__ import annotations

from research_council.debate.experimentation import run_experimentation
from research_council.store.models import ExperimentDraft
from research_council.verify.sandbox import LocalSandbox, build_sandbox


def test_local_sandbox_runs_and_captures():
    r = LocalSandbox().run("print('METRIC acc=0.9')")
    assert r.ok and r.exit_code == 0 and "METRIC acc=0.9" in r.stdout and not r.timed_out


def test_local_sandbox_reports_failure():
    r = LocalSandbox().run("raise ValueError('boom')")
    assert not r.ok and r.exit_code != 0 and "boom" in r.stderr


def test_local_sandbox_timeout():
    r = LocalSandbox().run("import time; time.sleep(5)", timeout=1)
    assert r.timed_out and not r.ok


def test_build_sandbox_refuses_unsafe_by_default(monkeypatch):
    import research_council.verify.sandbox as sb
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    s, warn = sb.build_sandbox("docker")               # no docker, not allowed → nothing
    assert s is None and "Docker" in warn
    s2, warn2 = sb.build_sandbox("docker", allow_local=True)  # explicit opt-in → local + warning
    assert isinstance(s2, sb.LocalSandbox) and "UNISOLATED" in warn2


class _FakeCoder:
    def __init__(self, scripts):
        self._scripts = iter(scripts)

    async def draft(self, idea, plan, *, error=""):
        return ExperimentDraft(code=next(self._scripts))


async def test_experimentation_feasible_first_try():
    res = await run_experimentation({"title": "X"}, "toy plan", _FakeCoder(["print('METRIC f1=0.62')"]),
                                    LocalSandbox(), max_attempts=2)
    assert res.ran and res.feasible and res.metric == "f1=0.62" and res.attempts == 1


async def test_experimentation_retries_then_succeeds():
    coder = _FakeCoder(["raise RuntimeError('bad')", "print('METRIC f1=0.5')"])
    res = await run_experimentation({"title": "X"}, "p", coder, LocalSandbox(), max_attempts=2)
    assert res.feasible and res.attempts == 2 and res.metric == "f1=0.5"


async def test_experimentation_ran_but_no_metric_is_not_feasible():
    res = await run_experimentation({"title": "X"}, "p", _FakeCoder(["x = 1  # no metric printed"]),
                                    LocalSandbox(), max_attempts=1)
    assert res.ran and not res.feasible and res.metric is None


async def test_coder_offline_via_testmodel():
    import pytest

    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.coder import Coder

    d = await Coder(TestModel()).draft({"title": "X"}, "plan")
    assert isinstance(d, ExperimentDraft)
