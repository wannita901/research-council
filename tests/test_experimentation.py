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


class _SpySandbox:
    """Counts how many times the sandbox actually executes — to prove empty code never
    burns a run. Delegates real execution to LocalSandbox for non-empty scripts."""

    name = "spy"

    def __init__(self):
        self.runs = 0

    def run(self, code, timeout=10, requirements=None):
        self.runs += 1
        return LocalSandbox().run(code, timeout=timeout)


class _CountingReviewer(_FakeReviewer):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = 0

    async def review(self, idea, plan, code, run):
        self.calls += 1
        return await super().review(idea, plan, code, run)


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


async def test_empty_code_never_runs_sandbox_or_reviewers():
    """Regression: a coder that returns an empty `code` field (the silent-failure mode that
    produced '# no code produced' across whole runs) must NOT waste a sandbox + review cycle.
    The loop should re-prompt instead, and the RQ ends infeasible — not falsely 'ran'."""
    sb = _SpySandbox()
    reviewers = [_CountingReviewer(approve=True, vendor=f"v{i}") for i in range(2)]
    res = await run_experimentation(
        {"title": "X"}, "plan", _FakeCoder(["", "", ""]), reviewers, sb, caps=_BALANCED
    )
    assert sb.runs == 0  # empty code never reached the sandbox
    assert all(r.calls == 0 for r in reviewers)  # nor the reviewers
    assert not res.feasible and not res.approved and res.iterations == 3


async def test_empty_code_then_valid_recovers():
    """The empty-code guard re-prompts; the next non-empty draft runs normally and can pass."""
    sb = _SpySandbox()
    res = await run_experimentation(
        {"title": "X"},
        "plan",
        _FakeCoder(["", "print('METRIC acc=0.8')"]),
        _approvers(2),
        sb,
        caps=_BALANCED,
    )
    assert sb.runs == 1  # only the non-empty attempt executed
    assert res.feasible and res.approved and res.metric == "acc=0.8"


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


def test_is_nonfinite_metric():
    from research_council.debate.experimentation import _is_nonfinite_metric

    assert _is_nonfinite_metric("loss=nan") is True
    assert _is_nonfinite_metric("loss=inf") is True
    assert _is_nonfinite_metric("loss=-inf") is True
    assert _is_nonfinite_metric("acc=0.9") is False  # finite number
    assert _is_nonfinite_metric("label=converged") is False  # categorical, not our concern
    assert _is_nonfinite_metric(None) is False
    assert _is_nonfinite_metric("") is False


class _StubSandbox:
    """Returns a fixed stdout/exit — lets the council loop be driven without a python binary."""

    name = "stub"

    def __init__(self, stdout, *, ok=True):
        self._stdout, self._ok = stdout, ok

    def run(self, code, *, timeout=30, requirements=None):
        from research_council.verify.sandbox import SandboxResult

        return SandboxResult(self._ok, 0 if self._ok else 1, self._stdout, "", 0.0, False, "stub")


async def test_nonfinite_metric_is_not_feasible():
    # exit 0 + a METRIC line, but the value is NaN → numerically degenerate, NOT feasible.
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _FakeCoder(["print('METRIC loss=nan')"]),
        _approvers(2),
        _StubSandbox("METRIC loss=nan\n"),
        caps=_ONE,
    )
    assert res.ran and not res.feasible and not res.approved
    assert res.metric == "loss=nan"  # metric kept for transparency, just not counted feasible


async def test_finite_metric_still_feasible_via_stub():
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _FakeCoder(["print('METRIC acc=0.9')"]),
        _approvers(2),
        _StubSandbox("METRIC acc=0.9\n"),
        caps=_ONE,
    )
    assert res.feasible and res.approved


def test_metrics_of_captures_all_lines_deduped_first_seen():
    from research_council.debate.experimentation import _metric_of, _metrics_of

    out = (
        "noise\nMETRIC f1=0.85\nMETRIC precision=0.71\n"
        "METRIC f1=0.85\nMETRIC f1_baseline=0.48\n"  # duplicate headline collapses
    )
    assert _metric_of(out) == "f1=0.85"  # headline = first, unchanged
    assert _metrics_of(out) == ["f1=0.85", "precision=0.71", "f1_baseline=0.48"]
    assert _metrics_of("no metric here") == []


_MULTI_STDOUT = "METRIC f1=0.85\nMETRIC precision=0.71\nMETRIC f1_baseline=0.48\n"


class _MultiMetricCoder:
    """Emits a headline metric plus two secondaries — a realistic multi-number experiment."""

    def __init__(self):
        self.usage = UsageMeter()

    async def draft(self, idea, plan, *, error="", prior_code="", feedback=""):
        return ExperimentDraft(code="print('multi')")


class _StdoutSandbox:
    """Offline fake sandbox returning fixed stdout (the real LocalSandbox needs a `python`
    binary, absent in this env). Lets the metric-capture wiring be verified deterministically."""

    def __init__(self, stdout):
        self._stdout = stdout

    def run(self, code, *, timeout=0, requirements=()):
        from research_council.verify.sandbox import SandboxResult

        return SandboxResult(
            ok=True,
            exit_code=0,
            stdout=self._stdout,
            stderr="",
            duration_s=0.0,
            timed_out=False,
            backend="fake",
        )


async def test_run_experimentation_records_all_metrics():
    res = await run_experimentation(
        {"title": "X"},
        "p",
        _MultiMetricCoder(),
        _approvers(2),
        _StdoutSandbox(_MULTI_STDOUT),
        caps=_BALANCED,
    )
    assert res.metric == "f1=0.85"  # headline unchanged → repro/approval contracts hold
    assert res.metrics == ["f1=0.85", "precision=0.71", "f1_baseline=0.48"]


async def test_write_experiments_emits_metrics_csv(tmp_path):
    from research_council.debate.experimentation import run_experiments, write_experiments
    from research_council.store.models import ResearchQuestion

    rqs = [ResearchQuestion(id="rq1", question="q1", plan="p1", metrics="f1")]
    res = await run_experiments(
        {"title": "X"},
        rqs,
        _MultiMetricCoder(),
        _approvers(2),
        _StdoutSandbox(_MULTI_STDOUT),
        caps=_BALANCED,
    )
    exp = write_experiments(res, tmp_path)
    # results.csv stays one HEADLINE row per RQ (approval/repro contract) ...
    results_csv = (exp / "results.csv").read_text()
    assert results_csv.count("\nrq1,") == 1 and "f1" in results_csv
    # ... while metrics.csv carries every captured METRIC.
    metrics_csv = (exp / "metrics.csv").read_text()
    assert metrics_csv.splitlines()[0] == "rq_id,metric,value"
    assert "rq1,f1,0.85" in metrics_csv
    assert "rq1,precision,0.71" in metrics_csv
    assert "rq1,f1_baseline,0.48" in metrics_csv


def test_load_evidence_merges_metrics_csv_and_backs_secondary_claim(tmp_path):
    """A paper number that matches a SECONDARY metric (only in metrics.csv) is now backed."""
    from research_council.verify import claims

    exp = tmp_path / "experiment"
    exp.mkdir(parents=True)
    # results.csv: one headline row per RQ (the existing contract)
    (exp / "results.csv").write_text(
        "rq_id,question,metric,value,feasible,approved\nrq1,q,f1,0.85,True,True\n"
    )
    # metrics.csv: headline + secondaries; headline duplicate must collapse
    (exp / "metrics.csv").write_text(
        "rq_id,metric,value\nrq1,f1,0.85\nrq1,precision,0.71\nrq1,f1_baseline,0.48\n"
    )
    ev = claims.load_evidence(tmp_path)
    values = sorted(e.value for e in ev)
    assert values == [0.48, 0.71, 0.85]  # f1=0.85 deduped, secondaries present

    report = claims.check_paper(
        "## Results\nThe model reached F1 0.85, with precision 0.71 over a 0.48 baseline.",
        ev,
    )
    assert report.n_unbacked == 0  # 0.71 and 0.48 now have a recorded source


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
