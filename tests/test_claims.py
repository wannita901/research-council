"""Claims-to-evidence checker (plan/25 Gap 1) — offline, no API keys.

The headline test reproduces the smoking gun from project …103845: the Results section
states "0.81 at N=2 to 0.57 at N=16" with no such value in results.csv, while the F / η² /
log-odds numbers it reports ARE in the data. The checker must back the latter and flag the
former."""

from __future__ import annotations

from pathlib import Path

from research_council.store.models import PaperDraft
from research_council.verify.claims import (
    Evidence,
    check_draft,
    check_paper,
    claims_to_change_requests,
    draft_to_audit_md,
    extract_claims,
    extract_suppressed,
    load_evidence,
    split_sections,
    write_claims_report,
)

PROJECTS = Path(__file__).resolve().parent / "fixtures" / "verify"
SMOKING_GUN = PROJECTS / "how-does-accuracy-scale-with-103845"


# --- number parsing / extraction ----------------------------------------------
def test_extract_decimals_percent_and_scientific_notation():
    text = "accuracy 0.81 falls to 0.57, a 12.5% drop, with coeff 1.37×10⁻⁴."
    claims = extract_claims(text, "Results")
    vals = {c.text: round(c.value, 6) for c in claims}
    assert vals["0.81"] == 0.81
    assert vals["0.57"] == 0.57
    assert vals["12.5%"] == 12.5
    assert round([c for c in claims if "10" in c.text][0].value, 6) == 0.000137


def test_bare_integers_are_not_treated_as_claims():
    # N=2, N=16, 50 tasks, the year 2024 — design params / counts, not measured results.
    claims = extract_claims("We ran N=16 over 50 tasks in 2024.", "Results")
    assert claims == []


def test_significance_thresholds_are_not_claims():
    # p<0.05 / α=0.01 are conventions, not measured values — must not be flagged.
    claims = extract_claims("Effect was significant (p<0.05, α = 0.01).", "Results")
    assert [c.text for c in claims] == []


def test_narrative_pvalue_and_threshold_are_suppressed():
    # results.csv records point metrics, not inferential statistics: a p-value reported in prose
    # ("p-value of 0.14") and a significance threshold named narratively ("the 0.05 threshold",
    # "corrected 0.00139 threshold") are conventions/statistics, not values to find in the data.
    text = (
        "The minimum uncorrected p-value of 0.14 falls short of the 0.05 threshold, "
        "let alone the corrected 0.00139 threshold."
    )
    assert extract_claims(text, "Results") == []


def test_dispersion_statistics_are_suppressed():
    # SD / SE / ± / CI are derived from the run; results.csv stores only the point estimate, so
    # flagging them would wrongly imply fabrication. The point estimate itself stays a claim.
    text = (
        "The grand mean is 0.35 (SD across cells = 0.08); cluster-robust SEs (≈0.09–0.10) "
        "exceed the naive i.i.d. SEs (≈0.06), within a CI of 0.04."
    )
    vals = {c.text for c in extract_claims(text, "Results")}
    assert vals == {"0.35"}  # the dispersion companions 0.08 / 0.09 / 0.10 / 0.06 / 0.04 are gone


def test_genuine_subaggregate_point_value_still_flagged():
    # Scope boundary: a per-cell point value the paper asserts but that isn't in results.csv is a
    # genuine point claim and MUST stay flagged — suppression is only for significance/dispersion.
    evidence = [Evidence(metric="grand_mean", value=0.35)]
    rep = check_paper("## Results\nCell accuracy ranged from 0.27 to 0.43.", evidence)
    assert {c.text for c in rep.unbacked} == {"0.27", "0.43"}


def test_192231_prose_drops_dispersion_noise_but_keeps_real_gap():
    # Verbatim sentences from the honest pilot project …192231, whose results.csv records only
    # the grand mean (mean_cc_score = 0.3534). Pre-suppression the checker flagged 9 "unbacked"
    # numbers — almost all dispersion/p-value noise — burying the real signal. After suppression
    # only the genuine sub-aggregate cell value (0.43, absent from the grand-mean-only data) is
    # flagged, so the gate accuses fabrication, not honest statistical reporting.
    paper_md = (
        "## Abstract\n"
        "The grand mean chance-corrected score is 0.35 (SD across cells = 0.08). All "
        "pairwise comparisons are inconclusive under Fisher's exact test.\n\n"
        "## Results\n"
        "Scores range from 0.27 (K=16, T=20) to 0.43 (K=4, T=5). The minimum uncorrected "
        "p-value of 0.14 falls well short of the uncorrected 0.05 threshold, let alone the "
        "corrected 0.00139 threshold. Cluster-robust SEs (≈0.09–0.10) are substantially "
        "larger than naive i.i.d. SEs (≈0.06)."
    )
    evidence = [
        Evidence(metric="mean_cc_score", value=0.3534),
        Evidence(metric="mean_chance_corrected_accuracy", value=0.2666),  # backs the 0.27 cell
    ]
    rep = check_paper(paper_md, evidence)
    assert {c.text for c in rep.unbacked} == {"0.43"}
    assert "0.35" in {c.text for c in rep.backed}  # the grand mean still matches the data


# --- suppressed numbers are recorded, not silently dropped ---------------------
def test_extract_suppressed_returns_conventions_with_reasons():
    # The numbers extract_claims skips are now retrievable, each tagged with WHY they were skipped.
    text = "The grand mean is 0.35 (SD = 0.08); the effect was significant (p<0.05)."
    sup = extract_suppressed(text, "Results")
    by_text = {c.text: c.suppress_reason for c in sup}
    assert by_text == {"0.08": "dispersion", "0.05": "significance"}
    # The point estimate is NOT in the suppressed set — it's a real claim extract_claims keeps.
    assert "0.35" not in by_text
    assert {c.text for c in extract_claims(text, "Results")} == {"0.35"}


def test_suppressed_numbers_surface_in_report_without_changing_verdict():
    # A fabricated SD that no metric backs no longer vanishes: it's recorded in report.suppressed
    # (backed=False) so a reviewer can SEE it, but it does NOT count as unbacked (verdict unchanged).
    evidence = [Evidence(metric="grand_mean", value=0.35)]
    rep = check_paper("## Results\nGrand mean 0.35 (SD = 0.08, p<0.05).", evidence)
    assert rep.n_unbacked == 0  # the gate still passes — suppressed numbers don't fail it
    sup = {c.text: c for c in rep.suppressed}
    assert set(sup) == {"0.08", "0.05"}
    assert sup["0.08"].suppress_reason == "dispersion"
    assert sup["0.08"].backed is False  # no recorded metric backs the made-up SD


def test_suppressed_dispersion_is_marked_backed_when_a_metric_verifies_it():
    # Since iter-14, metrics.csv can record secondary metrics like an SD. A suppressed dispersion
    # number that MATCHES a recorded value is positively verified (backed=True) instead of being
    # blindly trusted — turning "we didn't check this" into "this checks out".
    evidence = [Evidence(metric="acc_mean", value=0.35), Evidence(metric="acc_sd", value=0.08)]
    rep = check_paper("## Results\nAccuracy 0.35 (SD = 0.08).", evidence)
    sd = next(c for c in rep.suppressed if c.text == "0.08")
    assert sd.backed is True
    assert sd.matched_metric == "acc_sd"


def test_suppressed_list_persists_in_claims_json(tmp_path):
    paper = tmp_path / "paper"
    paper.mkdir(parents=True)
    (paper / "paper.md").write_text(
        "## Results\nGrand mean 0.35 (SD = 0.08, p<0.05).\n", encoding="utf-8"
    )
    exp = tmp_path / "experiment"
    exp.mkdir()
    (exp / "results.csv").write_text("rq_id,metric,value\nrq1,grand_mean,0.35\n", encoding="utf-8")
    rep = write_claims_report(tmp_path)
    assert rep is not None
    import json

    data = json.loads((paper / "claims.json").read_text(encoding="utf-8"))
    assert data["n_suppressed"] == 2
    assert {s["text"] for s in data["suppressed"]} == {"0.08", "0.05"}


# --- rounding-aware matching ---------------------------------------------------
def test_rounding_aware_match_backs_a_rounded_claim():
    evidence = [Evidence(metric="interaction_F", value=5.0812)]
    report = check_paper("## Results\nThe test gives F=5.08 overall.\n", evidence)
    assert [c.text for c in report.backed] == ["5.08"]
    assert report.backed[0].matched_metric == "interaction_F"
    assert not report.unbacked


def test_scientific_notation_backs_decimal_evidence():
    evidence = [Evidence(metric="signed_NxT_interaction", value=0.000137)]
    report = check_paper("## Results\ncoefficient of 1.37×10⁻⁴ (p<0.5).\n", evidence)
    backed = {c.text for c in report.backed}
    assert "1.37×10⁻⁴" in backed


def test_unbacked_number_is_flagged():
    evidence = [Evidence(metric="interaction_F", value=5.0812)]
    report = check_paper("## Results\nefficiency falls to 0.57 here.\n", evidence)
    assert [c.text for c in report.unbacked] == ["0.57"]
    assert not report.backed


# --- section scoping (no false positives from Related Work) --------------------
def test_related_work_numbers_are_not_audited():
    # 0.92 belongs to prior work; it should NOT be required to be in results.csv.
    paper = "## Related Work\nPrior systems reach 0.92 accuracy.\n## Results\nWe report 0.50.\n"
    report = check_paper(paper, evidence=[])
    flagged = {c.text for c in report.unbacked}
    assert "0.92" not in flagged  # Related Work is not audited
    assert "0.50" in flagged  # Results is audited, and there is no evidence


def test_conclusion_findings_are_audited():
    # The Conclusion restates the paper's OWN headline result, so a fabricated number placed
    # ONLY in the Conclusion must be flagged (it used to escape the Abstract+Results-only audit).
    paper = (
        "## Abstract\nWe study X.\n"
        "## Results\nWe report 0.50.\n"
        "## Conclusion\nOur method reaches 0.91 accuracy.\n"
    )
    report = check_paper(paper, evidence=[Evidence(metric="acc", value=0.50)])
    flagged = {(c.section, c.text) for c in report.unbacked}
    assert ("Conclusion", "0.91") in flagged  # fabricated headline, now caught
    assert ("Results", "0.50") not in flagged  # 0.50 is backed by the acc evidence


def test_discussion_findings_are_audited():
    # Discussion is the other own-findings section some venues use; same rule as Conclusion.
    paper = "## Results\nWe report 0.50.\n## Discussion\nThe effect size of 0.88 is large.\n"
    report = check_paper(paper, evidence=[Evidence(metric="acc", value=0.50)])
    assert ("Discussion", "0.88") in {(c.section, c.text) for c in report.unbacked}


def test_split_sections_ignores_title_and_byline():
    paper = "# My Title\n*by council*\n## Abstract\nwe did X\n## Results\nF=5.08\n"
    secs = split_sections(paper)
    assert set(secs) == {"Abstract", "Results"}
    assert "F=5.08" in secs["Results"]


# --- real fixture: the smoking gun --------------------------------------------
def test_load_evidence_from_real_results_csv():
    evidence = load_evidence(SMOKING_GUN)
    metrics = {e.metric: e.value for e in evidence}
    assert metrics == {
        "signed_NxT_interaction": 0.000137,
        "interaction_effect_size": 0.39,
        "interaction_F": 5.0812,
    }


def test_smoking_gun_backs_real_numbers_and_flags_fabricated_ones():
    evidence = load_evidence(SMOKING_GUN)
    paper_md = (SMOKING_GUN / "paper" / "paper.md").read_text(encoding="utf-8")
    report = check_paper(paper_md, evidence)

    backed = {c.text for c in report.backed}
    unbacked = {c.text for c in report.unbacked}

    # The three numbers that ARE in results.csv must be recognised as backed.
    assert "5.08" in backed  # interaction_F = 5.0812
    assert "0.39" in backed  # interaction_effect_size = 0.3900
    assert "1.37×10⁻⁴" in backed  # signed_NxT_interaction = 0.000137

    # The fabricated efficiency figures must be flagged as unbacked.
    assert "0.81" in unbacked
    assert "0.57" in unbacked
    assert report.n_unbacked >= 2


def test_change_requests_target_the_right_section():
    evidence = load_evidence(SMOKING_GUN)
    paper_md = (SMOKING_GUN / "paper" / "paper.md").read_text(encoding="utf-8")
    report = check_paper(paper_md, evidence)
    crs = claims_to_change_requests(report)
    assert crs, "expected change-requests for the unbacked claims"
    assert all(c.section in ("Abstract", "Results", "Conclusion", "Discussion") for c in crs)
    assert all(c.severity == "medium" for c in crs)  # v1: flag, not block
    assert any("0.81" in c.msg or "0.57" in c.msg for c in crs)


# --- artifact writing ----------------------------------------------------------
def test_write_claims_report_emits_json(tmp_path: Path):
    paper = tmp_path / "paper"
    exp = tmp_path / "experiment"
    paper.mkdir()
    exp.mkdir()
    (paper / "paper.md").write_text(
        "# T\n## Results\nWe report F=5.08 and a fabricated 0.99.\n", encoding="utf-8"
    )
    (exp / "results.csv").write_text(
        "rq_id,question,metric,value,feasible,approved,approvals,iterations,stopped_reason,backend\n"
        'rq1,"Does it, with commas, work?",interaction_F,5.0812,True,False,0,3,iters_exhausted,docker\n',
        encoding="utf-8",
    )
    report = write_claims_report(tmp_path)
    assert report is not None
    out = paper / "claims.json"
    assert out.exists()
    import json

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["n_backed"] == 1
    assert data["n_unbacked"] == 1
    assert data["unbacked"][0]["text"] == "0.99"


def test_write_claims_report_none_without_paper(tmp_path: Path):
    assert write_claims_report(tmp_path) is None


def test_parse_number_rejects_non_finite():
    from research_council.verify.claims import _parse_number

    # finite values still parse
    assert _parse_number("0.81") == 0.81
    assert _parse_number("1.37e-4") == 0.000137
    # NaN / ±inf (and overflow to inf) are rejected so they can't poison claims.json
    for tok in ("inf", "-inf", "Infinity", "nan", "NaN", "1e400"):
        assert _parse_number(tok) is None, tok


def test_non_finite_metric_keeps_claims_json_valid(tmp_path: Path):
    """A degenerate experiment that recorded a non-finite metric must not emit a bare
    NaN/Infinity token into claims.json (invalid JSON for strict readers)."""
    import json
    import math

    paper = tmp_path / "paper"
    exp = tmp_path / "experiment"
    paper.mkdir()
    exp.mkdir()
    (paper / "paper.md").write_text("# T\n## Results\nWe report an F1 of 0.81.\n", encoding="utf-8")
    (exp / "results.csv").write_text(
        "rq_id,metric,value\nrq1,accuracy,inf\nrq2,loss,nan\nrq3,f1,0.81\n",
        encoding="utf-8",
    )
    write_claims_report(tmp_path)
    text = (paper / "claims.json").read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text

    def _reject(tok):  # strict RFC-8259 reader: NaN/Infinity are not valid JSON
        raise ValueError(tok)

    data = json.loads(text, parse_constant=_reject)
    # Only the finite metric survived; the non-finite rows were dropped.
    assert all(math.isfinite(e["value"]) for e in data["evidence"])
    assert [(e["rq_id"], e["metric"], e["value"]) for e in data["evidence"]] == [
        ("rq3", "f1", 0.81)
    ]


# --- in-memory draft checking (wired into the writing loop) --------------------
def test_draft_to_audit_md_stitches_abstract_and_results_only():
    draft = PaperDraft(
        title="T",
        abstract="we report 0.62",
        sections={"Introduction": "ignore 0.11", "Results": "F=5.08", "Method": "skip 0.22"},
    )
    md = draft_to_audit_md(draft)
    # Only the audited sections (Abstract + Results) are stitched in.
    assert "## Abstract" in md and "0.62" in md
    assert "## Results" in md and "5.08" in md
    assert "Introduction" not in md and "Method" not in md


def test_check_draft_flags_fabricated_number_in_draft():
    evidence = [Evidence(metric="interaction_F", value=5.0812)]
    draft = PaperDraft(
        title="T",
        abstract="we summarise the study",
        sections={"Results": "The test gives F=5.08 but efficiency falls to 0.99."},
    )
    report = check_draft(draft, evidence)
    assert "5.08" in {c.text for c in report.backed}
    assert "0.99" in {c.text for c in report.unbacked}
    crs = claims_to_change_requests(report)
    assert any("0.99" in c.msg and c.section == "Results" for c in crs)
