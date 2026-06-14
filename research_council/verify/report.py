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
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from research_council.verify import approval, bib, claims, repro

# Figure references the paper points the reader to: markdown ![alt](path) and LaTeX
# \includegraphics[..]{path}. A reference that resolves to no file on disk is a broken
# evidence link (and breaks the \includegraphics compile) — exactly what _check_figures audits.
_MD_FIG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_TEX_FIG_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")

# Inline citation tokens in the prose. The writer emits ``[key]`` (and comma-joined
# ``[k1, k2]``) which latex.py::_cite rewrites to ``\cite{key}``; a key absent from the
# bibliography compiles to a dangling "[?]"/raw-key in the PDF — a broken evidence link.
# A citation key is bibtex-style: starts with a letter, then word/:/.-/ chars, no spaces — so
# this skips pure-numeric list markers ``[1]`` and (via the negative lookahead/strip below)
# markdown links ``[text](url)``.
_CITE_BRACKET_RE = re.compile(r"\[([A-Za-z][^\]\n]*?)\](?!\()")
_CITE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:_.\-]*$")
# Lines that DEFINE a reference key in paper.md's References section: "- [key] free text".
_REF_DEF_RE = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]")
# The headings (## Section) whose bodies define/host references or figures rather than cite
# them — excluded from the inline-citation scan so a definition isn't mistaken for a citation.
_NON_PROSE_SECTIONS = {"references", "figures"}

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
        return CheckResult("claims", SKIP, "no results.csv evidence to check claims against")
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
    pinning each experiment by sha256. Partial coverage degrades to WARN.

    Presence alone is not enough: each manifest's recorded ``code_sha256`` must still match the
    ``experiment.py`` on disk. If a script was edited or swapped after the manifest was written
    (or the manifest is stale), the pinned hash no longer describes the shipped code — the
    recorded metric is unreproducible — so a hash mismatch is a FAIL, not a silent PASS."""
    exp = out_dir / "experiment"
    if not exp.exists():
        return CheckResult("reproducible", SKIP, "no experiment/ directory")
    script = exp / "reproduce.sh"
    manifests = sorted(exp.glob("*/repro.json"))
    violations = repro.check_code_integrity(out_dir) if manifests else []
    details = {
        "reproduce_sh": script.exists(),
        "n_manifests": len(manifests),
        "rqs": [m.parent.name for m in manifests],
        "code_integrity_violations": violations,
    }
    if not script.exists() and not manifests:
        return CheckResult(
            "reproducible",
            FAIL,
            "no reproduce.sh or repro.json — runs are not re-runnable",
            details,
        )
    if violations:
        rqs = ", ".join(v["rq"] for v in violations)
        return CheckResult(
            "reproducible",
            FAIL,
            f"{len(violations)} manifest(s) ({rqs}) pin a code hash that no longer matches experiment.py",
            details,
        )
    if script.exists() and manifests:
        return CheckResult(
            "reproducible",
            PASS,
            f"reproduce.sh + {len(manifests)} per-RQ manifest(s) present, code hashes verified",
            details,
        )
    return CheckResult(
        "reproducible", WARN, "reproduction artifacts present but incomplete", details
    )


def _check_references(out_dir: Path) -> CheckResult:
    """references.bib must exist and resolve at least one citation to a real DOI/record;
    an all-UNVERIFIED bib is a WARN (visible gap), a missing bib with citations a FAIL.

    The counts are read back from references.bib ITSELF (the PRIMARY artifact a reader/CI
    inspects) — not from the run-time references.json, which may be stale or absent. The json
    is read only as a corroborating signal (e.g. to detect that any citation existed when the
    .bib is missing entirely)."""
    paper_dir = out_dir / "paper"
    bib_path = paper_dir / "references.bib"
    refs_json = paper_dir / "references.json"
    json_total = json_resolved = None
    if refs_json.exists():
        try:
            data = json.loads(refs_json.read_text(encoding="utf-8"))
            json_total = data.get("n_total")
            json_resolved = data.get("n_resolved")
        except (ValueError, OSError):
            pass
    if bib_path.exists():
        n_total, n_resolved = bib.count_resolved_bib(bib_path.read_text(encoding="utf-8"))
        details = {
            "references_bib": True,
            "n_total": n_total,
            "n_resolved": n_resolved,
            "json_n_total": json_total,
            "json_n_resolved": json_resolved,
        }
        if n_total == 0:
            # Header-only bib (the paper cites nothing) → nothing to verify, never a falsehood.
            return CheckResult(
                "references", SKIP, "references.bib present but lists no citations", details
            )
        if n_resolved == 0:
            return CheckResult(
                "references",
                WARN,
                f"references.bib present but 0/{n_total} resolved to a DOI/record",
                details,
            )
        return CheckResult(
            "references",
            PASS,
            f"{n_resolved}/{n_total} citation(s) resolved to a DOI/record",
            details,
        )
    # No bib on disk: fall back to the json only to tell "no citations" (SKIP) from
    # "citations existed but the verifiable artifact is missing" (FAIL).
    details = {"references_bib": False, "json_n_total": json_total, "json_n_resolved": json_resolved}
    if not json_total:
        return CheckResult("references", SKIP, "no citations / references.bib", details)
    return CheckResult(
        "references", FAIL, f"{json_total} citation(s) but no references.bib", details
    )


def _gather_figure_refs(out_dir: Path) -> list[str]:
    """Collect every local figure path the paper references, from paper.md (markdown image
    syntax) and paper.tex (\\includegraphics). External http(s) URLs are not local assets and
    are excluded — this check is about the figures the paper ships, not links it cites."""
    paper_dir = out_dir / "paper"
    refs: list[str] = []
    for fname, pattern in (("paper.md", _MD_FIG_RE), ("paper.tex", _TEX_FIG_RE)):
        f = paper_dir / fname
        if f.exists():
            for m in pattern.findall(f.read_text(encoding="utf-8")):
                ref = m.strip()
                if ref and not ref.lower().startswith(("http://", "https://")):
                    refs.append(ref)
    return refs


def _check_figures(out_dir: Path) -> CheckResult:
    """Every figure the paper points the reader to must resolve to a file on disk. A
    dangling reference is a broken evidence link — the reader is sent to evidence that
    isn't there, and the LaTeX \\includegraphics would fail to compile — so it FAILs."""
    paper_dir = out_dir / "paper"
    if not (paper_dir / "paper.md").exists() and not (paper_dir / "paper.tex").exists():
        return CheckResult("figures", SKIP, "no paper.md/paper.tex to audit")
    refs = _gather_figure_refs(out_dir)
    # Dedup while preserving order; figures embedded in both .md and .tex would double-count.
    seen: dict[str, None] = {}
    for r in refs:
        seen.setdefault(r, None)
    refs = list(seen)
    if not refs:
        return CheckResult("figures", SKIP, "paper references no figures", {"n_refs": 0})
    missing = [r for r in refs if not (paper_dir / r).exists()]
    details = {"n_refs": len(refs), "n_missing": len(missing), "missing": missing}
    if missing:
        return CheckResult(
            "figures",
            FAIL,
            f"{len(missing)}/{len(refs)} referenced figure(s) missing from disk — broken evidence link",
            details,
        )
    return CheckResult(
        "figures", PASS, f"all {len(refs)} referenced figure(s) present on disk", details
    )


def _prose_without_ref_sections(paper_md: str) -> str:
    """The paper body with the ``## References`` / ``## Figures`` sections stripped — those
    DEFINE keys (``- [key] text``) and image paths, they don't CITE, so scanning them for
    inline citations would mistake a definition for a use."""
    lines = paper_md.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("## "):
            skipping = line[3:].strip().lower() in _NON_PROSE_SECTIONS
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _defined_citation_keys(out_dir: Path) -> set[str]:
    """The keys the paper actually provides a reference for: every entry in references.bib
    (the verifiable artifact, preferred) UNION the ``- [key]`` lines of paper.md's References
    section (so a paper that lists references inline but predates the bib producer still has a
    valid key set to check against)."""
    paper_dir = out_dir / "paper"
    keys: set[str] = set()
    bib_path = paper_dir / "references.bib"
    if bib_path.exists():
        keys |= {e["key"] for e in bib.parse_bib_entries(bib_path.read_text(encoding="utf-8"))}
    paper_md = paper_dir / "paper.md"
    if paper_md.exists():
        in_refs = False
        for line in paper_md.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                in_refs = line[3:].strip().lower() == "references"
            elif in_refs:
                m = _REF_DEF_RE.match(line)
                if m:
                    keys.add(m.group(1).strip())
    return keys


def _cited_keys(out_dir: Path) -> list[str]:
    """Every inline citation key the paper's PROSE points the reader to (order-preserving,
    deduped). Handles comma-joined ``[k1, k2]`` brackets and ignores bracketed text that isn't
    a bibtex-style key (so list markers / glossed phrases aren't treated as citations)."""
    paper_md = out_dir / "paper" / "paper.md"
    if not paper_md.exists():
        return []
    prose = _prose_without_ref_sections(paper_md.read_text(encoding="utf-8"))
    seen: dict[str, None] = {}
    for body in _CITE_BRACKET_RE.findall(prose):
        for tok in body.split(","):
            key = tok.strip()
            if _CITE_KEY_RE.match(key):
                seen.setdefault(key, None)
    return list(seen)


def _check_citations(out_dir: Path) -> CheckResult:
    """Every inline ``[key]`` citation in the prose must have a matching reference entry. A key
    with no entry is a dangling citation — the reader is sent to a source that isn't in the
    bibliography, and it compiles to a dangling ``\\cite`` — so it FAILs, mirroring _check_figures.

    Closed-world by construction: a citation key the writer can't back can't silently appear in
    a verified paper. SKIP when there's no paper or the prose cites nothing."""
    paper_md = out_dir / "paper" / "paper.md"
    if not paper_md.exists():
        return CheckResult("citations", SKIP, "no paper.md to audit")
    cited = _cited_keys(out_dir)
    if not cited:
        return CheckResult("citations", SKIP, "paper cites no references inline", {"n_cited": 0})
    defined = _defined_citation_keys(out_dir)
    dangling = [k for k in cited if k not in defined]
    details = {
        "n_cited": len(cited),
        "n_defined": len(defined),
        "n_dangling": len(dangling),
        "dangling": dangling,
    }
    if dangling:
        return CheckResult(
            "citations",
            FAIL,
            f"{len(dangling)}/{len(cited)} inline citation(s) have no reference entry "
            f"({', '.join(dangling)}) — dangling citation / broken evidence link",
            details,
        )
    return CheckResult(
        "citations",
        PASS,
        f"all {len(cited)} inline citation(s) resolve to a reference entry",
        details,
    )


_NO_ENGINE_MARKER = "no tectonic/latexmk on PATH"


def _check_pdf(out_dir: Path) -> CheckResult:
    """The headline artifact. A paper.tex with no paper.pdf is a build failure, not 'no
    paper' — surfaced as FAIL so a broken Stage-C compile can't pass as verified.

    The one case that is NOT a falsehood is "no TeX engine was available to compile":
    latex.py records that as a non-failure (status ``fallback_no_tex``) and writes the
    marker ``no tectonic/latexmk on PATH`` to build.log. Treating that as FAIL would mark
    every engine-less machine (CI without tectonic/latexmk) UNVERIFIED purely for a missing
    local binary — so it degrades to SKIP, never blocking the verdict."""
    paper_dir = out_dir / "paper"
    pdf, tex = paper_dir / "paper.pdf", paper_dir / "paper.tex"
    if pdf.exists():
        kb = pdf.stat().st_size / 1024
        return CheckResult("pdf", PASS, f"paper.pdf compiled ({kb:.0f} KB)", {"kb": round(kb, 1)})
    if tex.exists():
        from research_council.verify.latex import latex_engine

        build_log = paper_dir / "build.log"
        log_text = (
            build_log.read_text(encoding="utf-8", errors="replace") if build_log.exists() else ""
        )
        # build.log is the run-time record of WHY there's no PDF. The no-engine marker (or, for
        # pre-producer projects with no log, no engine on PATH now) means the compile never ran —
        # not that it ran and failed.
        if _NO_ENGINE_MARKER in log_text or (not log_text and latex_engine()[0] is None):
            return CheckResult(
                "pdf", SKIP, "paper.tex present; no TeX engine available to compile it", {}
            )
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
        _check_citations(out),
        _check_figures(out),
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
