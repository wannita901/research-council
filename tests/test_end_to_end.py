"""End-to-end: the REAL producers emit a project the AUDITOR certifies VERIFIED (offline).

Every per-stage gate (claims / approval / repro / bib / latex) has been tested in isolation,
and ``verify_project`` has been tested on hand-built synthetic input. What was never proven is
the chain: that driving the actual Stage-B producer (``write_experiments`` → results.csv +
reproduce.sh + repro.json) and the actual Stage-C producer (``run_writing`` → paper.md +
claims.json + references.bib) yields a project that the project-level auditor accepts.

This is that proof — the positive counterpart to test_report's live-project tests, which show
the *pre-existing* outputs are UNVERIFIED. A fresh run through the gates with an honest writer
comes back VERIFIED. Fakes stand in for the LLM writer/reviewers and the bibliographic
providers, so it runs with no API keys, no Docker, and no network."""

from __future__ import annotations

import json
import shutil

import pytest

from research_council.debate.caps import StageCCaps
from research_council.debate.experimentation import write_experiments
from research_council.debate.writing import run_writing
from research_council.obs.telemetry import UsageMeter
from research_council.store.models import (
    Citation,
    ExperimentResult,
    Paper,
    PaperDraft,
    RQResult,
    StageHandoff,
)
from research_council.verify.report import FAIL, PASS, SKIP, verify_project

# The single source of truth for the experiment's metric. The writer is only allowed to state
# this number; results.csv records it; the claims auditor diffs prose↔csv. Keeping them tied to
# one constant is what makes the chain honest rather than a coincidence of literals.
_METRIC_NAME = "interaction_F"
_METRIC_VALUE = 5.0812


def _approved_rq() -> RQResult:
    """One council-APPROVED research question with a real metric and a seeded script — exactly
    the shape write_experiments persists to results.csv + the per-RQ repro.json."""
    return RQResult(
        rq_id="rq1",
        question="Does the interaction effect hold, with commas, under scale?",
        result=ExperimentResult(
            ran=True,
            feasible=True,
            approved=True,  # ← the approval gate reads this back out of results.csv
            approvals=2,
            iterations=3,
            stopped_reason="approved",
            backend="docker",
            metric=f"METRIC {_METRIC_NAME}={_METRIC_VALUE}",
            code=(
                "import random\n"
                "random.seed(42)\n"  # detectable seed → repro manifest marks deterministic
                f"print('METRIC {_METRIC_NAME}={_METRIC_VALUE}')\n"
            ),
            requirements=["numpy"],
        ),
    )


class _BackedWriter:
    """A writer that only states numbers the data supports: Abstract+Results cite F=5.08, which
    rounds-matches the recorded 5.0812. Carries the allowed citations through so the bib
    resolver has something real to resolve."""

    def __init__(self):
        self.usage = UsageMeter()

    async def draft(
        self, idea, experiment, constraints=None, *, allowed_citations=None, figures=None
    ):
        return PaperDraft(
            title="An Honest Study of the Interaction Effect",
            abstract="We measure an interaction effect of F=5.08 and report it faithfully.",
            sections={
                "Introduction": "Prior work motivates the question.",
                "Method": "We run a controlled experiment under scale.",
                "Results": "The interaction effect is significant (F=5.08).",
                "Conclusion": "The effect holds.",
            },
            citations=list(allowed_citations or []),
            figures=list(figures or []),
        )

    async def revise(self, draft, change_requests, sections):
        return draft.model_copy(deep=True)

    async def coherence_pass(self, draft):
        return draft


class _Reviewer:
    def __init__(self, score, vendor):
        self.usage = UsageMeter()
        self._score = score
        self.vendor = vendor

    async def review(self, draft, rubric):
        from research_council.store.models import ReviewNotes

        return ReviewNotes(
            scores={k: self._score for k in rubric},
            comments=["solid"],
            verdict="accept",
            reviewer_vendor=self.vendor,
        )


class _ResolvingProvider:
    """A bibliographic provider that returns a record whose title matches the citation and whose
    url carries a real DOI — so resolve_citation attaches the DOI and the reference resolves."""

    name = "openalex"

    async def search(self, query, k=10):
        return [
            Paper(
                id="W123",
                title=query,  # exact title → similarity 1.0, well above MATCH_THRESHOLD
                year=2024,
                url="https://doi.org/10.1145/3597926.3598012",
                source="openalex",
            )
        ]


def _handoff() -> StageHandoff:
    return StageHandoff(
        from_stage="experimentation",
        to_stage="writing",
        idea={
            "title": "Interaction Effect",
            "hypothesis": "h",
            "method": "m",
            "experiment_plan": "p",
        },
        experiment_plan="p",
        constraints={},
        artifacts={"ran": True, "feasible": True, "metric": f"{_METRIC_NAME}={_METRIC_VALUE}"},
    )


# Thorough profile: every gate set to BLOCK, so this run proves a verifiable paper survives the
# strictest configuration — not merely that the flags are off.
_THOROUGH = StageCCaps(
    max_revisions=2,
    accept=0.70,
    usd_budget=0.0,
    claims_unbacked_block=True,
    unapproved_block=True,
)


async def _run_pipeline(tmp_path, *, latex: bool):
    write_experiments(
        [_approved_rq()], tmp_path
    )  # Stage B: results.csv + reproduce.sh + repro.json
    citations = [
        Citation(key="repair24", text="Automated Program Repair with Large Language Models")
    ]
    reviewers = [_Reviewer(0.9, "a"), _Reviewer(0.9, "b")]
    return await run_writing(
        _handoff(),
        _BackedWriter(),
        reviewers,
        venue="icse",
        out_dir=tmp_path,
        caps=_THOROUGH,
        allowed_citations=citations,
        bib_providers=[_ResolvingProvider()],
        latex=latex,
    )


async def test_producers_yield_a_verified_project(tmp_path):
    """The full chain: an honest paper on an approved experiment, audited end-to-end, is
    VERIFIED. claims/approval/reproducible/references all PASS; pdf SKIPs (not typeset) without
    blocking the verdict."""
    res = await _run_pipeline(tmp_path, latex=False)
    assert res.accepted and res.stopped_reason == "accepted"
    assert res.claims_unbacked == 0  # the writer stated only backed numbers
    assert res.approved_rqs == res.total_rqs == 1  # the council approved the experiment
    assert res.refs_resolved == 1  # the citation resolved to a real DOI

    report = verify_project(tmp_path)
    by_name = {c.name: c.status for c in report.checks}
    assert by_name["claims"] == PASS
    assert by_name["approval"] == PASS
    assert by_name["reproducible"] == PASS
    assert by_name["references"] == PASS
    assert by_name["pdf"] == SKIP  # latex=False → no paper.tex → un-audited, not failed
    assert report.verdict == "verified"
    assert report.n_fail == 0 and report.n_warn == 0


async def test_one_fabricated_number_flips_the_same_run_to_unverified(tmp_path):
    """Control: the chain isn't vacuously green. Injecting a number with no row in results.csv
    after the run makes the auditor return UNVERIFIED — proving the PASS above is load-bearing."""
    await _run_pipeline(tmp_path, latex=False)
    paper_md = tmp_path / "paper" / "paper.md"
    poisoned = paper_md.read_text(encoding="utf-8").replace(
        "The interaction effect is significant (F=5.08).",
        "The interaction effect is significant (F=5.08); accuracy hits 0.991.",
    )
    paper_md.write_text(poisoned, encoding="utf-8")

    report = verify_project(tmp_path)
    by_name = {c.name: c for c in report.checks}
    assert by_name["claims"].status == FAIL
    assert "0.991" in " ".join(by_name["claims"].details["unbacked"])
    assert report.verdict == "unverified"


@pytest.mark.skipif(
    not (shutil.which("latexmk") or shutil.which("tectonic")),
    reason="no LaTeX engine — PDF compilation not exercisable in this environment",
)
async def test_producers_yield_a_compiled_pdf_when_a_tex_engine_is_present(tmp_path):
    """When a real TeX engine is available the same chain compiles paper.pdf, so the pdf check
    PASSES and the verdict is still VERIFIED — the headline artifact, end-to-end."""
    res = await _run_pipeline(tmp_path, latex=True)
    assert res.latex == "built" and res.pdf_path

    report = verify_project(tmp_path)
    by_name = {c.name: c.status for c in report.checks}
    assert by_name["pdf"] == PASS
    assert report.verdict == "verified"
    # the scorecard records the compiled PDF's size as evidence
    pdf_check = next(c for c in report.checks if c.name == "pdf")
    assert pdf_check.details["kb"] > 0


async def test_verification_json_is_a_persisted_artifact(tmp_path):
    """write_report drops paper/verification.json so the verdict is itself an inspectable
    artifact alongside the paper the producers just generated."""
    from research_council.verify.report import write_report

    await _run_pipeline(tmp_path, latex=False)
    report = write_report(tmp_path, project="e2e")
    path = tmp_path / "paper" / "verification.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "e2e"
    assert data["verdict"] == report.verdict == "verified"
