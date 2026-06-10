"""Stage B engine (plan/18) — council loop: implement → run → multi-agent review → revise.

One author (coder) drafts a script; the sandbox runs it; two cross-vendor reviewers review
the code AND the result, emitting typed findings and an approve/reject verdict (a reviewer
may attach a verification probe that the sandbox runs as evidence). The author revises against
stderr + blocking findings and we re-run.

The loop stops when the result is FEASIBLE (ran + emitted `METRIC name=value`) AND at least
`k` of the 2 reviewers approve — or when the iteration cap / USD budget binds, in which case
the best-so-far result is returned with an honest `feasible`/`approved` flag.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from research_council.debate.caps import StageBCaps, stage_b_caps, total_spend
from research_council.store.models import CodeReview, ExperimentResult, RQResult

_METRIC = re.compile(r"METRIC\s+([^\s=]+)\s*=\s*(\S+)")
Emit = Callable[[str, str, dict], None] | None


def _metric_of(stdout: str) -> str | None:
    m = _METRIC.search(stdout or "")
    return f"{m.group(1)}={m.group(2)}" if m else None


def write_experiment(result: ExperimentResult, out_dir: Path | str) -> Path:
    """Materialize the Stage-B artifacts under <out_dir>/experiment/ — the code the council
    actually ran, the result summary, the run log, and the code reviews. Returns the dir."""
    d = Path(out_dir) / "experiment"
    d.mkdir(parents=True, exist_ok=True)
    (d / "experiment.py").write_text(result.code or "# no code produced\n", encoding="utf-8")
    (d / "log.txt").write_text(result.log or "", encoding="utf-8")

    res_md = [
        "# Experiment result",
        "",
        f"- **feasible:** {result.feasible}  (ran + emitted a METRIC)",
        f"- **approved:** {result.approved}  ({result.approvals} of the reviewers)",
        f"- **metric:** {result.metric or '—'}",
        f"- **iterations:** {result.iterations}",
        f"- **stopped:** {result.stopped_reason}",
        f"- **backend:** {result.backend}",
        f"- **cost_usd:** {result.usd:.4f}",
    ]
    (d / "result.md").write_text("\n".join(res_md) + "\n", encoding="utf-8")
    (d / "reviews.md").write_text(_reviews_md(result), encoding="utf-8")
    return d


def _better(a: ExperimentResult | None, b: ExperimentResult) -> ExperimentResult:
    """Prefer approved, then feasible, then more approvals, then later iteration."""
    if a is None:
        return b

    def key(r):
        return (r.approved, r.feasible, r.approvals, r.iterations)

    return b if key(b) > key(a) else a


async def _run_probe(
    review: CodeReview, sandbox, timeout: int, emit: Emit, attempt: int
) -> CodeReview:
    """Run at most one reviewer probe in the sandbox and record it as evidence."""
    for f in review.findings:
        if f.probe and (f.probe.code or "").strip():
            res = sandbox.run(f.probe.code, timeout=timeout)
            f.probe.ran = res.ok
            f.probe.output = (res.stdout or res.stderr or f"exit {res.exit_code}")[-500:]
            f.probe.supports = res.ok  # it executed → the reviewer's demonstration stands
            if emit:
                emit(
                    "experiment",
                    "review_probe",
                    {
                        "attempt": attempt,
                        "vendor": review.reviewer_vendor,
                        "kind": f.kind,
                        "ran": res.ok,
                        "backend": res.backend,
                    },
                )
            break  # cap: one probe per reviewer per iteration
    return review


def _feedback(stderr: str, reviews: list[CodeReview]) -> tuple[str, str]:
    """(execution error, reviewer-findings text) to feed the next revision."""
    blockers, others = [], []
    for rv in reviews:
        for f in rv.findings:
            line = f"[{f.severity} {f.kind}] {f.msg} → fix: {f.fix}"
            if f.probe and f.probe.ran:
                line += f" (probe evidence: {f.probe.output[:120]})"
            (blockers if f.blocking else others).append(line)
    notes = "\n".join(blockers + others)
    return stderr, notes


async def run_experimentation(
    idea: dict,
    plan: str,
    coder,
    reviewers,
    sandbox,
    *,
    caps: StageBCaps | None = None,
    profile: str = "balanced",
    budget_base: float = 0.0,
    prior_code: str = "",
    prior_feedback: str = "",
    emit: Emit = None,
) -> ExperimentResult:
    """`budget_base` is the spend already incurred before this experiment — the per-experiment
    budget is enforced against the marginal spend (spent − budget_base), so each RQ gets its own
    allowance even when the same agents accumulate usage across RQs.

    `prior_code`/`prior_feedback` seed the FIRST attempt when re-running a stage that already has
    artifacts — the coder improves the prior script (and sees the prior reviews/fail log) instead
    of drafting from scratch."""
    caps = caps or stage_b_caps(profile)
    code, err, notes, last_ran = prior_code, "", prior_feedback, False
    best: ExperimentResult | None = None

    for attempt in range(1, caps.max_iters + 1):
        draft = await coder.draft(idea, plan, error=err, prior_code=code, feedback=notes)
        code = draft.code
        if emit:
            emit(
                "experiment",
                "code_drafted",
                {"attempt": attempt, "chars": len(code), "notes": draft.notes},
            )

        res = sandbox.run(code, timeout=caps.timeout)
        last_ran = res.ok
        metric = _metric_of(res.stdout)
        feasible = bool(res.ok and metric)
        if emit:
            emit(
                "experiment",
                "sandbox_run",
                {
                    "attempt": attempt,
                    "ok": res.ok,
                    "exit_code": res.exit_code,
                    "timed_out": res.timed_out,
                    "feasible": feasible,
                    "backend": res.backend,
                },
            )

        reviews: list[CodeReview] = []
        for rv in reviewers:
            review = await rv.review(idea, plan, code, res)
            review = await _run_probe(review, sandbox, caps.probe_timeout, emit, attempt)
            reviews.append(review)
            if emit:
                emit(
                    "experiment",
                    "code_review",
                    {
                        "attempt": attempt,
                        "vendor": review.reviewer_vendor,
                        "approve": review.approve,
                        "blocker": review.has_blocker,
                        "findings": [f.model_dump() for f in review.findings],
                    },
                )

        approvals = sum(1 for rv in reviews if rv.approve and not rv.has_blocker)
        approved = feasible and approvals >= caps.k
        spent = total_spend(coder, *reviewers)
        cur = ExperimentResult(
            ran=last_ran,
            feasible=feasible,
            metric=metric,
            attempts=attempt,
            iterations=attempt,
            code=code,
            log=(res.stdout or res.stderr or f"exit {res.exit_code}")[-1200:],
            backend=res.backend,
            approved=approved,
            approvals=approvals,
            reviews=reviews,
            usd=spent,
        )
        best = _better(best, cur)

        if approved:
            cur.stopped_reason = "approved"
            if emit:
                emit(
                    "experiment",
                    "approved",
                    {"attempt": attempt, "approvals": approvals, "usd": spent},
                )
            return cur

        if caps.usd_budget and (spent - budget_base) >= caps.usd_budget:
            best.stopped_reason = "budget_exhausted"
            if emit:
                emit("experiment", "budget_exhausted", {"attempt": attempt, "usd": spent})
            return best

        err, notes = _feedback(res.stderr or "", reviews)

    assert best is not None
    best.stopped_reason = "iters_exhausted"
    if emit:
        emit(
            "experiment",
            "iters_exhausted",
            {"iterations": caps.max_iters, "approvals": best.approvals},
        )
    return best


async def run_experiments(
    idea: dict,
    rqs,
    coder,
    reviewers,
    sandbox,
    *,
    caps: StageBCaps | None = None,
    profile: str = "balanced",
    prior: dict | None = None,
    emit: Emit = None,
) -> list[RQResult]:
    """Run one council loop per research question (RQ-driven Stage B, plan/21). The same agents
    are reused across RQs but each RQ gets its own per-experiment budget allowance.

    `prior` (rq_id → {code, feedback}) seeds each RQ from a previous run so the council improves
    the existing experiment instead of rebuilding it."""
    caps = caps or stage_b_caps(profile)
    prior = prior or {}
    out: list[RQResult] = []
    for rq in rqs:
        base = total_spend(coder, *reviewers)  # spend before this RQ → per-RQ budget
        seed = prior.get(rq.id, {})
        if emit:
            emit(
                "experiment",
                "rq_start",
                {"rq_id": rq.id, "question": rq.question, "continuing": bool(seed.get("code"))},
            )
        res = await run_experimentation(
            idea,
            rq.plan or idea.get("experiment_plan", ""),
            coder,
            reviewers,
            sandbox,
            caps=caps,
            budget_base=base,
            prior_code=seed.get("code", ""),
            prior_feedback=seed.get("feedback", ""),
            emit=emit,
        )
        out.append(RQResult(rq_id=rq.id, question=rq.question, result=res))
        if emit:
            emit(
                "experiment",
                "rq_done",
                {
                    "rq_id": rq.id,
                    "feasible": res.feasible,
                    "approved": res.approved,
                    "metric": res.metric,
                },
            )
    return out


def _metric_parts(metric: str | None) -> tuple[str, str]:
    if metric and "=" in metric:
        n, _, v = metric.partition("=")
        return n.strip(), v.strip()
    return ("metric", metric or "")


def write_experiments(rq_results: list[RQResult], out_dir: Path | str) -> Path:
    """Materialize per-RQ artifacts under <out_dir>/experiment/<rq>/ plus an aggregated
    results.csv (one row per RQ metric). Returns the experiment dir."""
    import csv

    exp = Path(out_dir) / "experiment"
    exp.mkdir(parents=True, exist_ok=True)
    rows = []
    for rr in rq_results:
        sub = exp / rr.rq_id
        sub.mkdir(parents=True, exist_ok=True)
        r = rr.result
        (sub / "experiment.py").write_text(r.code or "# no code produced\n", encoding="utf-8")
        (sub / "log.txt").write_text(r.log or "", encoding="utf-8")
        (sub / "reviews.md").write_text(_reviews_md(r), encoding="utf-8")
        (sub / "question.md").write_text(
            f"# {rr.rq_id.upper()}\n\n{rr.question}\n", encoding="utf-8"
        )
        name, val = _metric_parts(r.metric)
        rows.append(
            {
                "rq_id": rr.rq_id,
                "question": rr.question,
                "metric": name,
                "value": val,
                "feasible": r.feasible,
                "approved": r.approved,
                "approvals": r.approvals,
                "iterations": r.iterations,
                "stopped_reason": r.stopped_reason,
                "backend": r.backend,
            }
        )
    with (exp / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "rq_id",
                "question",
                "metric",
                "value",
                "feasible",
                "approved",
                "approvals",
                "iterations",
                "stopped_reason",
                "backend",
            ],
        )
        w.writeheader()
        w.writerows(rows)
    return exp


def load_prior_experiments(out_dir: Path | str) -> dict:
    """Read a previous Stage-B run's per-RQ artifacts so a re-run can IMPROVE them.
    Returns {rq_id: {"code", "feedback"}} from <out_dir>/experiment/<rq>/."""
    exp = Path(out_dir) / "experiment"
    prior: dict = {}
    if not exp.is_dir():
        return prior
    for sub in sorted(exp.iterdir()):
        if not sub.is_dir():
            continue
        code = (
            (sub / "experiment.py").read_text(encoding="utf-8")
            if (sub / "experiment.py").exists()
            else ""
        )
        reviews = (
            (sub / "reviews.md").read_text(encoding="utf-8")
            if (sub / "reviews.md").exists()
            else ""
        )
        log = (sub / "log.txt").read_text(encoding="utf-8") if (sub / "log.txt").exists() else ""
        fb = "Prior reviewer findings to address:\n" + reviews if reviews else ""
        if log.strip():
            fb += f"\n\nPrior run log (for debugging):\n{log[-800:]}"
        if code.strip():
            prior[sub.name] = {"code": code, "feedback": fb.strip()}
    return prior


def _reviews_md(result: ExperimentResult) -> str:
    rv = ["# Code reviews", ""]
    for r in result.reviews:
        rv.append(
            f"## {r.reviewer_vendor or 'reviewer'} — {'approve' if r.approve else 'request changes'}"
        )
        if r.summary:
            rv.append(r.summary)
        for f in r.findings:
            rv.append(
                f"- **[{f.severity} {f.kind}]** {f.msg}" + (f" → _fix:_ {f.fix}" if f.fix else "")
            )
            if f.probe and f.probe.ran:
                rv.append(
                    f"  - probe ({'supports' if f.probe.supports else 'inconclusive'}): {f.probe.output[:200]}"
                )
        rv.append("")
    return "\n".join(rv) + "\n"
