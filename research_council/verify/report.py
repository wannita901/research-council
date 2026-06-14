"""Project-level verifiability scorecard (plan/25 Gap 6 capstone) — offline, no API keys.

The per-stage verify primitives each emit their own artifact *during* a run:
claims.py → claims.json, approval.py → the approval tally, bib.py → references.bib,
repro.py → reproduce.sh / repro.json, latex.py → paper.pdf. Nothing yet reads a
*finished* project back and renders ONE verdict on whether its artifacts are verifiable.

This module is that read-back audit. It RE-RUNS the checks against the PRIMARY artifacts
(paper.md + experiment/results.csv) rather than trusting the run-time json — so the verdict
is an independent re-audit (and works on the live projects/*, which predate the producers).
Powers ``council project verify <pid>`` and writes paper/verification.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from research_council.verify import approval, claims

# Per-check status vocabulary. PASS = verifiable; FAIL = a verifiability defect that a
# reader could catch (fabricated number, paper on unapproved experiments, no PDF);
# WARN = degraded but not a falsehood; SKIP = the input for this check is absent.
PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"


@dataclass
class CheckResult:
    name: str
    status: str
    summary: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerifiabilityReport:
    """The scorecard written to paper/verification.json."""

    project: str = ""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def n_fail(self) -> int:
        return sum(1 for c in self.checks if c.status == FAIL)

    @property
    def n_warn(self) -> int:
        return sum(1 for c in self.checks if c.status == WARN)

    @property
    def verdict(self) -> str:
        """``verified`` when no check FAILs; ``verified-with-warnings`` when only WARNs
        remain; ``unverified`` when any check FAILs. SKIPs never block — a missing input
        is not a falsehood, just an un-audited dimension."""
        if self.n_fail:
            return "unverified"
        if self.n_warn:
            return "verified-with-warnings"
        return "verified"

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "verdict": self.verdict,
            "n_fail": self.n_fail,
            "n_warn": self.n_warn,
            "checks": [c.to_dict() for c in self.checks],
        }


# --- individual checks --------------------------------------------------------
def _check_claims(out_dir: Path) -> CheckResult:
    """Re-extract numeric claims from the paper's Abstract+Results and diff against
    results.csv. Any unbacked claim is a FAIL — that's the fabricated-number smoking gun."""
    paper_md = out_dir / "paper" / "paper.md"
    if not paper_md.exists():
        return CheckResult("claims", SKIP, "no paper.md to audit")
    evidence = claims.load_evidence(out_dir)
    if not evidence:
        return CheckResult(
            "claims", SKIP, "no results.csv evidence to check claims against"
        )
    report = claims.check_paper(paper_md.read_text(encoding="utf-8"), evidence)
    details = {
        "n_claims": report.n_claims,
        "n_backed": len(report.backed),
        "n_unbacked": report.n_unbacked,
        "unbacked": [c.text for c in report.unbacked],
    }
    if report.n_unbacked:
        return CheckResult(
            "claims",
            FAIL,
            f"{report.n_unbacked}/{report.n_claims} numeric claim(s) have no row in results.csv",
            details,
        )
    if report.n_claims == 0:
        return CheckResult("claims", WARN, "no numeric claims found to verify", details)
    return CheckResult(
        "claims", PASS, f"all {report.n_claims} numeric claim(s) backed by results.csv", details
    )


def _check_approval(out_dir: Path) -> CheckResult:
    """Read the council's own ``approved`` column back: a paper resting on experiments the
    council never approved is overclaiming unless it's framed as feasibility/negative."""
    status = approval.approval_status(out_dir)
    details = status.to_dict()
    if not status.has_results:
        return CheckResult("approval", SKIP, "no results.csv — no approval signal")
    if status.all_approved:
        return CheckResult(
            "approval", PASS, f"all {status.total} RQ(s) approved by the council", details
        )
    if not status.any_approved:
        return CheckResult(
            "approval",
            FAIL,
            f"0/{status.total} RQ(s) approved — paper rests on unapproved experiments",
            details,
        )
    return CheckResult(
        "approval",
        WARN,
        f"{status.approved}/{status.total} RQ(s) approved — must be framed honestly",
        details,
    )


def _check_reproducible(out_dir: Path) -> CheckResult:
    """A re-runnable artifact needs the top-level reproduce.sh plus a per-RQ repro.json
    pinning each experiment by sha256. Partial coverage degrades to WARN."""
    exp = out_dir / "experiment"
    if not exp.exists():
        return CheckResult("reproducible", SKIP, "no experiment/ directory")
    script = exp / "reproduce.sh"
    manifests = sorted(exp.glob("*/repro.json"))
    details = {
        "reproduce_sh": script.exists(),
        "n_manifests": len(manifests),
        "rqs": [m.parent.name for m in manifests],
    }
    if not script.exists() and not manifests:
        return CheckResult(
            "reproducible", FAIL, "no reproduce.sh or repro.json — runs are not re-runnable", details
        )
    if script.exists() and manifests:
        return CheckResult(
            "reproducible",
            PASS,
            f"reproduce.sh + {len(manifests)} per-RQ manifest(s) present",
            details,
        )
    return CheckResult(
        "reproducible", WARN, "reproduction artifacts present but incomplete", details
    )


def _check_references(out_dir: Path) -> CheckResult:
    """references.bib must exist and resolve at least one citation to a real DOI/record;
    an all-UNVERIFIED bib is a WARN (visible gap), a missing bib with citations a FAIL."""
    paper_dir = out_dir / "paper"
    bib = paper_dir / "references.bib"
    refs_json = paper_dir / "references.json"
    n_total = n_resolved = None
    if refs_json.exists():
        try:
            data = json.loads(refs_json.read_text(encoding="utf-8"))
            n_total = data.get("n_total")
            n_resolved = data.get("n_resolved")
        except (ValueError, OSError):
            pass
    details = {"references_bib": bib.exists(), "n_total": n_total, "n_resolved": n_resolved}
    if not bib.exists():
        # No bib AND no evidence that any citation existed → nothing to verify.
        if not n_total:
            return CheckResult("references", SKIP, "no citations / references.bib", details)
        return CheckResult(
            "references", FAIL, f"{n_total} citation(s) but no references.bib", details
        )
    if n_resolved is not None and n_total:
        if n_resolved == 0:
            return CheckResult(
                "references", WARN, f"references.bib present but 0/{n_total} resolved to a DOI", details
            )
        return CheckResult(
            "references", PASS, f"{n_resolved}/{n_total} citation(s) resolved to a DOI/record", details
        )
    return CheckResult("references", PASS, "references.bib present", details)


def _check_pdf(out_dir: Path) -> CheckResult:
    """The headline artifact. A paper.tex with no paper.pdf is a build failure, not 'no
    paper' — surfaced as FAIL so a broken Stage-C compile can't pass as verified."""
    paper_dir = out_dir / "paper"
    pdf, tex = paper_dir / "paper.pdf", paper_dir / "paper.tex"
    if pdf.exists():
        kb = pdf.stat().st_size / 1024
        return CheckResult("pdf", PASS, f"paper.pdf compiled ({kb:.0f} KB)", {"kb": round(kb, 1)})
    if tex.exists():
        return CheckResult(
            "pdf", FAIL, "paper.tex present but no paper.pdf — LaTeX build failed", {}
        )
    return CheckResult("pdf", SKIP, "no paper.tex — paper not yet typeset")


# --- aggregate ----------------------------------------------------------------
def verify_project(out_dir: Path | str, *, project: str = "") -> VerifiabilityReport:
    """Run every verifiability check against a finished project on disk and aggregate a
    single verdict. Offline: reads only on-disk artifacts, never calls a provider."""
    out = Path(out_dir)
    report = VerifiabilityReport(project=project or out.name)
    report.checks = [
        _check_claims(out),
        _check_approval(out),
        _check_reproducible(out),
        _check_references(out),
        _check_pdf(out),
    ]
    return report


def write_report(out_dir: Path | str, *, project: str = "") -> VerifiabilityReport:
    """verify_project + persist paper/verification.json (the scorecard is itself an
    artifact a reader can inspect)."""
    out = Path(out_dir)
    report = verify_project(out, project=project)
    paper_dir = out / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "verification.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report
