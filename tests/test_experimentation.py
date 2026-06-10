"""Stage B — sandbox + council loop (offline; LocalSandbox runs controlled scripts)."""

from __future__ import annotations

from research_council.debate.caps import StageBCaps
from research_council.debate.experimentation import run_experimentation
from research_council.obs.telemetry import UsageMeter
from research_council.store.models import (
    CodeReview,
    ExperimentDraft,
    ReviewFinding,
    VerificationProbe,
)
from research_council.verify.sandbox import LocalSandbox


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
    s, warn = sb.build_sandbox("docker")  # no docker, not allowed → nothing
    assert s is None and "Docker" in warn
    s2, warn2 = sb.build_sandbox("docker", allow_local=True)  # explicit opt-in → local + warning
    assert isinstance(s2, sb.LocalSandbox) and "UNISOLATED" in warn2


class _FakeCoder:
    def __init__(self, scripts):
        self._scripts = iter(scripts)
        self.usage = UsageMeter()

    async def draft(self, idea, plan, *, error="", prior_code="", feedback=""):
        return ExperimentDraft(code=next(self._scripts))


class _FakeReviewer:
    def __init__(self, *, approve=True, findings=None, vendor="x", cost=0.0):
        self.vendor = vendor
        self.usage = UsageMeter(cost_usd=cost)
        self._approve, self._findings = approve, findings or []

    async def review(self, idea, plan, code, run):
        return CodeReview(
            reviewer_vendor=self.vendor,
            approve=self._approve,
            findings=[f.model_copy(deep=True) for f in self._findings],
        )


def _approvers(n=2):
    return [_FakeReviewer(approve=True, vendor=f"v{i}") for i in range(n)]


_BALANCED = StageBCaps(max_iters=3, k=2, usd_budget=0.0, timeout=5)
_ONE = StageBCaps(max_iters=1, k=2, usd_budget=0.0, timeout=5)


async def test_feasible_and_approved_first_try():
    res = await run_experimentation(
        {"title": "X"},
        "toy plan",
        _FakeCoder(["print('METRIC f1=0.62')"]),
        _approvers(2),
        LocalSandbox(),
        caps=_BALANCED,
    )
    assert res.feasible and res.approved and res.approvals == 2
    assert res.metric == "f1=0.62" and res.iterations == 1 and res.stopped_reason == "approved"


async def test_retries_then_approved():
    coder = _FakeCoder(["raise RuntimeError('bad')", "print('METRIC f1=0.5')"])
    res = await run_experimentation(
        {"title": "X"}, "p", coder, _approvers(2), LocalSandbox(), caps=_BALANCED
    )
    assert res.approved and res.iterations == 2 and res.metric == "f1=0.5"


async def test_feasible_but_blocked_is_not_approved():
    blocker = ReviewFinding(
        kind="soundness", severity="high", msg="reports train acc", fix="hold out test"
    )
    reviewers = [
        _FakeReviewer(approve=False, findings=[blocker], vendor="a"),
        _FakeReviewer(approve=True, vendor="b"),
    ]
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _FakeCoder(["print('METRIC f1=0.9')"]),
        reviewers,
        LocalSandbox(),
        caps=_ONE,
    )
    assert res.feasible and not res.approved and res.stopped_reason == "iters_exhausted"


async def test_ran_but_no_metric_is_not_feasible():
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _FakeCoder(["x = 1  # no metric"]),
        _approvers(2),
        LocalSandbox(),
        caps=_ONE,
    )
    assert res.ran and not res.feasible and res.metric is None


async def test_budget_exhausted_stops_loop():
    blocker = ReviewFinding(kind="correctness", severity="high", msg="bug", fix="fix it")
    reviewers = [
        _FakeReviewer(approve=False, findings=[blocker], vendor="a", cost=10.0),
        _FakeReviewer(approve=True, vendor="b"),
    ]
    caps = StageBCaps(max_iters=5, k=2, usd_budget=1.5, timeout=5)
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _FakeCoder(["print('METRIC f1=0.9')"] * 5),
        reviewers,
        LocalSandbox(),
        caps=caps,
    )
    assert res.stopped_reason == "budget_exhausted" and res.iterations == 1


async def test_verification_probe_runs_in_sandbox():
    probe = VerificationProbe(code="print('PROBE leak confirmed')")
    finding = ReviewFinding(kind="soundness", severity="high", msg="leak", fix="split", probe=probe)
    reviewers = [
        _FakeReviewer(approve=False, findings=[finding], vendor="a"),
        _FakeReviewer(approve=True, vendor="b"),
    ]
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _FakeCoder(["print('METRIC f1=0.9')"]),
        reviewers,
        LocalSandbox(),
        caps=_ONE,
    )
    rv = res.reviews[0]
    assert rv.findings[0].probe.ran and "leak confirmed" in rv.findings[0].probe.output


class _CyclingCoder:
    """Always returns a feasible script (doesn't exhaust across RQs/attempts)."""

    def __init__(self):
        self.usage = UsageMeter()

    async def draft(self, idea, plan, *, error="", prior_code="", feedback=""):
        return ExperimentDraft(code="print('METRIC acc=0.8')")


async def test_run_experiments_one_loop_per_rq_with_csv(tmp_path):
    from research_council.debate.experimentation import run_experiments, write_experiments
    from research_council.store.models import ResearchQuestion

    rqs = [
        ResearchQuestion(id="rq1", question="does it work?", plan="p1", metrics="acc"),
        ResearchQuestion(id="rq2", question="beats baseline?", plan="p2", metrics="acc"),
    ]
    results = await run_experiments(
        {"title": "X"}, rqs, _CyclingCoder(), _approvers(2), LocalSandbox(), caps=_BALANCED
    )
    assert len(results) == 2 and all(r.result.approved for r in results)
    assert results[0].rq_id == "rq1" and results[1].rq_id == "rq2"

    exp = write_experiments(results, tmp_path)
    assert (exp / "rq1" / "experiment.py").exists() and (exp / "rq2" / "reviews.md").exists()
    csv_text = (exp / "results.csv").read_text()
    assert csv_text.splitlines()[0].startswith("rq_id,question,metric,value")
    assert "rq1" in csv_text and "rq2" in csv_text and "acc" in csv_text and "0.8" in csv_text


def test_load_prior_experiments_reads_code_and_feedback(tmp_path):
    from research_council.debate.experimentation import load_prior_experiments

    d = tmp_path / "experiment" / "rq1"
    d.mkdir(parents=True)
    (d / "experiment.py").write_text("print('METRIC acc=0.7')")
    (d / "reviews.md").write_text("# Code reviews\n- [high soundness] uses train acc")
    (d / "log.txt").write_text("Traceback: boom")
    prior = load_prior_experiments(tmp_path)
    assert "rq1" in prior and "METRIC acc=0.7" in prior["rq1"]["code"]
    assert "train acc" in prior["rq1"]["feedback"] and "boom" in prior["rq1"]["feedback"]
    assert load_prior_experiments(tmp_path / "nope") == {}  # no dir → empty


async def test_run_experiments_continues_from_prior(tmp_path):
    from research_council.debate.experimentation import run_experiments
    from research_council.store.models import ResearchQuestion

    seen = {}

    class _RecordingCoder:
        def __init__(self):
            self.usage = UsageMeter()

        async def draft(self, idea, plan, *, error="", prior_code="", feedback=""):
            seen.setdefault("prior_code", prior_code)  # capture the FIRST draft's seed
            return ExperimentDraft(code="print('METRIC acc=0.9')")

    rqs = [ResearchQuestion(id="rq1", question="q", plan="p", metrics="acc")]
    prior = {"rq1": {"code": "print('OLD CODE')", "feedback": "fix the metric"}}
    await run_experiments(
        {"title": "X"},
        rqs,
        _RecordingCoder(),
        _approvers(2),
        LocalSandbox(),
        caps=_BALANCED,
        prior=prior,
    )
    assert seen["prior_code"] == "print('OLD CODE')"  # improved the prior, not from scratch


def test_local_sandbox_collects_figures():
    script = (
        "import os\n"
        "os.makedirs('figures', exist_ok=True)\n"
        "open('figures/plot.png', 'wb').write(b'PNGDATA')\n"
        "print('METRIC acc=0.9')\n"
    )
    r = LocalSandbox().run(script)
    assert r.ok and r.figures.get("plot.png") == b"PNGDATA"


async def test_run_experiments_saves_figures(tmp_path):
    from research_council.debate.experimentation import run_experiments, write_experiments
    from research_council.store.models import ResearchQuestion

    class _FigCoder:
        def __init__(self):
            self.usage = UsageMeter()

        async def draft(self, idea, plan, *, error="", prior_code="", feedback=""):
            return ExperimentDraft(
                code="import os\nos.makedirs('figures', exist_ok=True)\n"
                "open('figures/r.png', 'wb').write(b'PNG')\nprint('METRIC acc=0.9')\n"
            )

    rqs = [ResearchQuestion(id="rq1", question="q", plan="p", metrics="acc")]
    res = await run_experiments(
        {"title": "X"}, rqs, _FigCoder(), _approvers(2), LocalSandbox(), caps=_BALANCED
    )
    assert res[0].result.figures == ["r.png"]  # figure recorded on the result
    write_experiments(res, tmp_path)
    assert (tmp_path / "experiment" / "rq1" / "figures" / "r.png").read_bytes() == b"PNG"


def test_write_experiment_materializes_artifacts(tmp_path):
    from research_council.debate.experimentation import write_experiment
    from research_council.store.models import CodeReview, ExperimentResult, ReviewFinding

    res = ExperimentResult(
        ran=True,
        feasible=True,
        approved=True,
        approvals=2,
        iterations=2,
        metric="f1=0.62",
        code="print('METRIC f1=0.62')",
        log="METRIC f1=0.62",
        backend="local",
        stopped_reason="approved",
        reviews=[
            CodeReview(
                reviewer_vendor="openai",
                approve=True,
                findings=[ReviewFinding(kind="style", severity="low", msg="nit", fix="rename")],
            )
        ],
    )
    d = write_experiment(res, tmp_path)
    assert (d / "experiment.py").read_text().startswith("print(")
    result_md = (d / "result.md").read_text()
    assert "f1=0.62" in result_md and "approved:** True" in result_md
    assert "openai" in (d / "reviews.md").read_text()
    assert (d / "log.txt").exists()


async def test_coder_offline_via_testmodel():
    import pytest

    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.coder import Coder

    d = await Coder(TestModel()).draft({"title": "X"}, "plan")
    assert isinstance(d, ExperimentDraft)


async def test_code_reviewer_offline_via_testmodel():
    import pytest

    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.code_reviewer import CodeReviewer

    rv = CodeReviewer(TestModel(), vendor="openai")
    out = await rv.review(
        {"title": "X"}, "p", "print('METRIC f1=1.0')", LocalSandbox().run("print('hi')")
    )
    assert isinstance(out, CodeReview) and out.reviewer_vendor == "openai"
