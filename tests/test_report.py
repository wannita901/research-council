"""Project-level verifiability scorecard (plan/25 Gap 6 capstone) — offline, no API keys.

The headline tests run the aggregate audit against the two live projects: …103845 (the
smoking gun: fabricated numbers, 0 RQs approved, no PDF) must come back UNVERIFIED, while a
clean synthetic project must come back VERIFIED. The verdict is recomputed from the PRIMARY
artifacts (paper.md + results.csv), so it works on projects that predate the per-stage json."""

from __future__ import annotations

import json
from pathlib import Path

from research_council.verify.report import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    verify_project,
    write_report,
)

PROJECTS = Path(__file__).resolve().parent / "fixtures" / "verify"
SMOKING_GUN = PROJECTS / "how-does-accuracy-scale-with-103845"
GOOD = PROJECTS / "how-does-accuracy-scale-with-192231"


def _by_name(report):
    return {c.name: c for c in report.checks}


# --- the live smoking-gun project ---------------------------------------------
def test_smoking_gun_is_unverified():
    """…103845: fabricated 0.81/0.57, 0/N approved, no PDF → verdict UNVERIFIED."""
    report = verify_project(SMOKING_GUN)
    assert report.verdict == "unverified"
    assert report.n_fail >= 1
    checks = _by_name(report)
    assert checks["claims"].status == FAIL  # the fabricated numbers
    assert checks["approval"].status == FAIL  # 0 RQs approved
    assert checks["pdf"].status == FAIL  # paper.tex emitted but the LaTeX build failed


def test_smoking_gun_claims_lists_the_fabricated_numbers():
    report = verify_project(SMOKING_GUN)
    unbacked = _by_name(report)["claims"].details["unbacked"]
    blob = " ".join(unbacked)
    assert "0.81" in blob and "0.57" in blob


# --- the live compiled project ------------------------------------------------
def test_good_project_pdf_passes():
    """…192231 shipped a paper.pdf → the pdf check passes with a size."""
    report = verify_project(GOOD)
    pdf = _by_name(report)["pdf"]
    assert pdf.status == PASS
    assert pdf.details["kb"] > 0


# --- synthetic fully-verifiable project ---------------------------------------
def _clean_project(root: Path) -> Path:
    out = root / "clean"
    (out / "experiment" / "rq1").mkdir(parents=True)
    (out / "paper").mkdir(parents=True)
    (out / "experiment" / "results.csv").write_text(
        'rq_id,question,metric,value,approved\nrq1,"does it, work?",f1,0.873,true\n',
        encoding="utf-8",
    )
    (out / "paper" / "paper.md").write_text(
        "## Abstract\nWe report an F1 of 0.873.\n## Results\nThe model reaches 0.873 F1.\n",
        encoding="utf-8",
    )
    # reproduction artifacts
    (out / "experiment" / "reproduce.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (out / "experiment" / "rq1" / "repro.json").write_text("{}", encoding="utf-8")
    # references with a resolved DOI
    (out / "paper" / "references.bib").write_text(
        "@article{x, title={T}, doi={10.1145/1234567}}\n", encoding="utf-8"
    )
    (out / "paper" / "references.json").write_text(
        json.dumps({"n_total": 1, "n_resolved": 1}), encoding="utf-8"
    )
    # a compiled PDF
    (out / "paper" / "paper.tex").write_text("\\documentclass{article}", encoding="utf-8")
    (out / "paper" / "paper.pdf").write_text("%PDF-1.4 fake", encoding="utf-8")
    return out


def test_clean_project_is_verified(tmp_path):
    out = _clean_project(tmp_path)
    report = verify_project(out)
    assert report.verdict == "verified"
    assert report.n_fail == 0 and report.n_warn == 0
    statuses = {c.name: c.status for c in report.checks}
    assert statuses == {
        "claims": PASS,
        "approval": PASS,
        "reproducible": PASS,
        "references": PASS,
        "citations": SKIP,  # the clean fixture cites nothing inline → un-audited, not blocked
        "figures": SKIP,  # the clean fixture references no figures → un-audited, not blocked
        "pdf": PASS,
    }


def test_unbacked_claim_flips_clean_project_to_unverified(tmp_path):
    out = _clean_project(tmp_path)
    # inject a fabricated number into Results that has no row in results.csv
    (out / "paper" / "paper.md").write_text(
        "## Abstract\nWe report an F1 of 0.873.\n## Results\nAccuracy jumps to 0.991.\n",
        encoding="utf-8",
    )
    report = verify_project(out)
    assert report.verdict == "unverified"
    assert _by_name(report)["claims"].status == FAIL


# --- per-check edge cases -----------------------------------------------------
def test_missing_pdf_with_tex_is_fail(tmp_path):
    out = _clean_project(tmp_path)
    (out / "paper" / "paper.pdf").unlink()
    # A real build failure log (engine ran, errored) — NOT the no-engine marker — so the
    # FAIL path is exercised deterministically even on an engine-less machine (CI), where a
    # tex-without-pdf and no log would otherwise degrade to SKIP.
    (out / "paper" / "build.log").write_text("! LaTeX Error: build failed.\n", encoding="utf-8")
    report = verify_project(out)
    assert _by_name(report)["pdf"].status == FAIL
    assert report.verdict == "unverified"


def test_unresolved_references_warns_not_fails(tmp_path):
    out = _clean_project(tmp_path)
    # The PRIMARY artifact (the .bib) is what's audited: 3 entries, all tagged UNVERIFIED.
    (out / "paper" / "references.bib").write_text(
        "@misc{a, title={A}, note = {UNVERIFIED: no matching record found}}\n"
        "@misc{b, title={B}, note = {UNVERIFIED: no matching record found}}\n"
        "@misc{c, title={C}, note = {UNVERIFIED: no matching record found}}\n",
        encoding="utf-8",
    )
    (out / "paper" / "references.json").write_text(
        json.dumps({"n_total": 3, "n_resolved": 0}), encoding="utf-8"
    )
    report = verify_project(out)
    refs = _by_name(report)["references"]
    assert refs.status == WARN
    assert refs.details["n_total"] == 3 and refs.details["n_resolved"] == 0
    # a warn alone (no fail) keeps the project shippable
    assert report.verdict == "verified-with-warnings"


def test_references_audit_the_bib_not_the_stale_json(tmp_path):
    """The .bib is the primary artifact: a references.json claiming resolutions the on-disk
    .bib does NOT carry must not let the audit report PASS on phantom DOIs."""
    out = _clean_project(tmp_path)
    # All entries are UNVERIFIED on disk...
    (out / "paper" / "references.bib").write_text(
        "@misc{a, title={A}, note = {UNVERIFIED: no matching record found}}\n"
        "@misc{b, title={B}, note = {UNVERIFIED: no matching record found}}\n",
        encoding="utf-8",
    )
    # ...but a stale json claims everything resolved. The audit must believe the .bib.
    (out / "paper" / "references.json").write_text(
        json.dumps({"n_total": 2, "n_resolved": 2}), encoding="utf-8"
    )
    refs = _by_name(verify_project(out))["references"]
    assert refs.status == WARN
    assert refs.details["n_resolved"] == 0  # read from the .bib, not the json's 2


def test_references_pass_counts_from_bib_when_json_missing(tmp_path):
    """A .bib with resolved entries PASSes on its own — no references.json required."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").write_text(
        "@article{a, title={A}, doi={10.1145/1}, note = {resolved via openalex}}\n"
        "@misc{b, title={B}, note = {UNVERIFIED: no matching record found}}\n",
        encoding="utf-8",
    )
    (out / "paper" / "references.json").unlink()
    refs = _by_name(verify_project(out))["references"]
    assert refs.status == PASS
    assert refs.details["n_total"] == 2 and refs.details["n_resolved"] == 1


def test_header_only_bib_skips(tmp_path):
    """A bib with only the header comment (the paper cites nothing) → SKIP, not a false PASS."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").write_text(
        "% references.bib — generated by research-council\n", encoding="utf-8"
    )
    (out / "paper" / "references.json").unlink()
    refs = _by_name(verify_project(out))["references"]
    assert refs.status == SKIP


def test_empty_project_skips_everything(tmp_path):
    out = tmp_path / "empty"
    out.mkdir()
    report = verify_project(out)
    assert all(c.status == SKIP for c in report.checks)
    # nothing audited, nothing failed → not blocked
    assert report.verdict == "verified"


def test_partial_repro_artifacts_warn(tmp_path):
    out = _clean_project(tmp_path)
    (out / "experiment" / "reproduce.sh").unlink()  # manifests remain, script gone
    report = verify_project(out)
    assert _by_name(report)["reproducible"].status == WARN


def test_tampered_experiment_code_flips_reproducible_to_fail(tmp_path):
    """A manifest pins code_sha256, but the on-disk experiment.py was swapped after the run.
    The recorded metric no longer describes the shipped code → reproducible FAILs and an
    otherwise-clean project goes UNVERIFIED, instead of the pin being trusted blindly."""
    from research_council.store.models import ExperimentResult, RQResult
    from research_council.verify.repro import write_repro

    out = _clean_project(tmp_path)
    code = "import random\nrandom.seed(0)\nprint('METRIC f1=0.873')"
    rq = RQResult(
        rq_id="rq1",
        question="does it, work?",
        result=ExperimentResult(ran=True, feasible=True, approved=True, metric="f1=0.873", code=code),
    )
    exp = write_repro([rq], out)  # repro.json now carries the real sha256 of `code`
    (exp / "rq1" / "experiment.py").write_text(code + "\n# tampered\n", encoding="utf-8")
    report = verify_project(out)
    repro_check = _by_name(report)["reproducible"]
    assert repro_check.status == FAIL
    assert repro_check.details["code_integrity_violations"][0]["rq"] == "rq1"
    assert report.verdict == "unverified"


# --- figures ------------------------------------------------------------------
def test_present_figure_passes(tmp_path):
    """A paper that references a figure which exists on disk → figures PASS."""
    out = _clean_project(tmp_path)
    (out / "paper" / "assets").mkdir()
    (out / "paper" / "assets" / "result.png").write_bytes(b"\x89PNG fake")
    (out / "paper" / "paper.md").write_text(
        "## Abstract\nWe report an F1 of 0.873.\n## Results\nThe model reaches 0.873 F1.\n"
        "## Figures\n**Figure 1.**\n![figure 1](assets/result.png)\n",
        encoding="utf-8",
    )
    report = verify_project(out)
    fig = _by_name(report)["figures"]
    assert fig.status == PASS
    assert fig.details["n_refs"] == 1 and fig.details["n_missing"] == 0
    assert report.verdict == "verified"


def test_dangling_figure_reference_is_fail(tmp_path):
    """A paper that points to assets/result.png with no such file → broken evidence link → FAIL,
    flipping an otherwise-clean project to unverified."""
    out = _clean_project(tmp_path)
    (out / "paper" / "paper.md").write_text(
        "## Abstract\nWe report an F1 of 0.873.\n## Results\nThe model reaches 0.873 F1.\n"
        "## Figures\n![figure 1](assets/result.png)\n",
        encoding="utf-8",
    )
    report = verify_project(out)
    fig = _by_name(report)["figures"]
    assert fig.status == FAIL
    assert fig.details["missing"] == ["assets/result.png"]
    assert report.verdict == "unverified"


def test_no_figures_referenced_skips(tmp_path):
    """The clean fixture references no figures → SKIP (not a falsehood, just un-audited)."""
    out = _clean_project(tmp_path)
    assert _by_name(verify_project(out))["figures"].status == SKIP


def test_external_image_url_is_not_treated_as_a_local_asset(tmp_path):
    """An http(s) image link is a citation, not a shipped asset, so it never FAILs as missing."""
    out = _clean_project(tmp_path)
    (out / "paper" / "paper.md").write_text(
        "## Abstract\nWe report an F1 of 0.873.\n## Results\nSee 0.873.\n"
        "![remote](https://example.com/chart.png)\n",
        encoding="utf-8",
    )
    assert _by_name(verify_project(out))["figures"].status == SKIP


def test_latex_includegraphics_reference_is_audited(tmp_path):
    """A missing figure declared only via \\includegraphics in paper.tex is also caught."""
    out = _clean_project(tmp_path)
    (out / "paper" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\includegraphics[width=.7\\linewidth]{assets/result.png}\\end{document}",
        encoding="utf-8",
    )
    fig = _by_name(verify_project(out))["figures"]
    assert fig.status == FAIL and fig.details["missing"] == ["assets/result.png"]


# --- inline-citation integrity (dangling \cite / broken evidence link) --------
def _cite(out: Path, prose: str, refs: str = "") -> None:
    """Overwrite paper.md with a Related Work prose body + an optional References section."""
    body = f"## Abstract\nWe report an F1 of 0.873.\n## Related Work\n{prose}\n"
    if refs:
        body += f"## References\n{refs}\n"
    (out / "paper" / "paper.md").write_text(body, encoding="utf-8")


def test_dangling_inline_citation_is_fail(tmp_path):
    """A [key] cited in prose with no reference entry → FAIL (the reader is sent to a source
    that isn't in the bibliography). Reproduces the live …192231 gap inline."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").unlink()  # no bib at all, like …192231
    _cite(out, "Tools help [wei2022chain, yao2023react]. Depth matters [shinn2023reflexion].")
    report = verify_project(out)
    cit = _by_name(report)["citations"]
    assert cit.status == FAIL
    assert set(cit.details["dangling"]) == {"wei2022chain", "yao2023react", "shinn2023reflexion"}
    assert report.verdict == "unverified"  # the dangling cite flips the whole verdict


def test_inline_citation_backed_by_bib_passes(tmp_path):
    """Same prose, but the keys now have entries in references.bib → PASS."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").write_text(
        "@article{wei2022chain, title={Chain}, doi={10.1/x}}\n"
        "@misc{yao2023react, title={ReAct}, note={UNVERIFIED}}\n",
        encoding="utf-8",
    )
    _cite(out, "Tools help [wei2022chain] and acting [yao2023react].")
    cit = _by_name(verify_project(out))["citations"]
    assert cit.status == PASS and cit.details["n_cited"] == 2


def test_inline_citation_backed_by_md_reference_list_passes(tmp_path):
    """No bib, but the paper lists the key in its ## References section → PASS (pre-bib papers)."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").unlink()
    _cite(out, "Breadth matters [motivation].", refs="- [motivation] Some grounded wiki source\n")
    cit = _by_name(verify_project(out))["citations"]
    assert cit.status == PASS


def test_reference_definition_is_not_counted_as_a_citation(tmp_path):
    """The ``- [key]`` lines in the References section DEFINE keys; they must not themselves be
    scanned as inline citations (else every defined key would be its own dangling use)."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").unlink()
    # Prose cites nothing; only the References section mentions [orphan].
    _cite(out, "No citations in this prose.", refs="- [orphan] An uncited reference\n")
    assert _by_name(verify_project(out))["citations"].status == SKIP


def test_markdown_link_and_numeric_bracket_are_not_citations(tmp_path):
    """``[text](url)`` markdown links and pure-numeric ``[1]`` list markers are not bibtex
    keys — they must not be flagged as dangling citations."""
    out = _clean_project(tmp_path)
    (out / "paper" / "references.bib").unlink()
    _cite(out, "See [our repo](http://x) and item [1] and [2].")
    assert _by_name(verify_project(out))["citations"].status == SKIP


# NB: the committed …192231 fixture ships only paper.pdf (paper.md is gitignored/sanitised), so
# the live dangling-citation gap it exhibits is reproduced inline above (test_dangling_inline…)
# rather than asserted against the fixture, which would SKIP for lack of a paper.md.


# --- artifact + persistence ---------------------------------------------------
def test_write_report_persists_verification_json(tmp_path):
    out = _clean_project(tmp_path)
    write_report(out, project="clean")
    path = out / "paper" / "verification.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "clean"
    assert data["verdict"] == "verified"
    assert {c["name"] for c in data["checks"]} == {
        "claims",
        "approval",
        "reproducible",
        "references",
        "citations",
        "figures",
        "pdf",
    }


def test_write_report_creates_paper_dir(tmp_path):
    """A project that never reached Stage C has no paper/ dir — write_report must still
    land verification.json rather than crash."""
    out = tmp_path / "no_paper"
    (out / "experiment").mkdir(parents=True)
    (out / "experiment" / "results.csv").write_text(
        "rq_id,metric,value,approved\nrq1,f1,0.5,false\n", encoding="utf-8"
    )
    report = write_report(out)
    assert (out / "paper" / "verification.json").exists()
    # 0 approved with results → approval FAIL → unverified
    assert report.verdict == "unverified"
