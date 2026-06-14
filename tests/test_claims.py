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
    load_evidence,
    split_sections,
    write_claims_report,
)

PROJECTS = Path(__file__).resolve().parents[1] / "projects"
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
    assert all(c.section in ("Abstract", "Results") for c in crs)
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
