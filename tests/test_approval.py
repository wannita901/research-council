"""Stage B→C approval gate (plan/25 Gap 4) — offline, no API keys.

The headline case reproduces the second smoking gun from project …103845: every RQ was
recorded approved=False, yet Stage C drafted a full, venue-scored paper as if it were a
result. The gate must read that signal back from results.csv and (a) always frame the paper
honestly, (b) when blocking is enabled, refuse to ship it as `accepted`."""

from __future__ import annotations

from pathlib import Path

from test_writing import _FakeWriter, _handoff, _Reviewer  # reuse the loop harness

from research_council.debate.caps import StageCCaps, stage_c_caps
from research_council.debate.writing import run_writing
from research_council.verify.approval import (
    approval_status,
    approval_to_change_request,
    honesty_constraint,
)

PROJECTS = Path(__file__).resolve().parents[1] / "projects"
SMOKING_GUN = PROJECTS / "how-does-accuracy-scale-with-103845"

_HEADER = "rq_id,question,metric,value,feasible,approved,approvals,iterations,stopped_reason,backend\n"


def _write_results(out_dir: Path, rows: str) -> None:
    (out_dir / "experiment").mkdir(parents=True, exist_ok=True)
    (out_dir / "experiment" / "results.csv").write_text(_HEADER + rows, encoding="utf-8")


# --- approval_status ----------------------------------------------------------
def test_no_results_file_is_no_signal(tmp_path):
    st = approval_status(tmp_path)
    assert not st.has_results and st.approved == 0 and st.total == 0


def test_tallies_approved_and_unapproved(tmp_path):
    _write_results(
        tmp_path,
        'rq1,"q, with comma",m,0.5,True,True,2,3,approved,docker\n'
        'rq2,"q2",m,0.6,True,False,0,2,exhausted,docker\n'
        'rq3,"q3",m,0.7,True,True,2,1,approved,docker\n',
    )
    st = approval_status(tmp_path)
    assert st.total == 3 and st.approved == 2
    assert st.unapproved_rqs == ["rq2"]
    assert st.any_approved and not st.all_approved


def test_commas_inside_question_do_not_break_the_count(tmp_path):
    # the csv module (not cut -d,) must keep total==1 despite commas in the quoted question
    _write_results(tmp_path, 'rq1,"Does it, with many, commas, work?",m,0.5,True,False,0,2,x,docker\n')
    st = approval_status(tmp_path)
    assert st.total == 1 and st.approved == 0 and st.unapproved_rqs == ["rq1"]


def test_real_smoking_gun_has_zero_approved():
    if not (SMOKING_GUN / "experiment" / "results.csv").exists():
        return  # project fixture not present in this checkout
    st = approval_status(SMOKING_GUN)
    assert st.total >= 1 and st.approved == 0 and not st.any_approved


# --- honesty_constraint -------------------------------------------------------
def test_no_constraint_when_all_approved(tmp_path):
    _write_results(tmp_path, 'rq1,"q",m,0.5,True,True,2,3,approved,docker\n')
    assert honesty_constraint(approval_status(tmp_path)) is None


def test_no_constraint_without_results(tmp_path):
    assert honesty_constraint(approval_status(tmp_path)) is None


def test_zero_approved_yields_negative_result_framing(tmp_path):
    _write_results(tmp_path, 'rq1,"q",m,0.5,True,False,0,2,x,docker\n')
    c = honesty_constraint(approval_status(tmp_path))
    assert c and "feasibility" in c.lower() and "not overclaim" in c.lower()


def test_partial_approval_scopes_claims(tmp_path):
    _write_results(
        tmp_path,
        'rq1,"q",m,0.5,True,True,2,3,approved,docker\n'
        'rq2,"q2",m,0.6,True,False,0,2,x,docker\n',
    )
    c = honesty_constraint(approval_status(tmp_path))
    assert c and "rq2" in c and "approved" in c.lower()


# --- approval_to_change_request ----------------------------------------------
def test_change_request_only_when_zero_approved(tmp_path):
    _write_results(tmp_path, 'rq1,"q",m,0.5,True,True,2,3,approved,docker\n')
    assert approval_to_change_request(approval_status(tmp_path)) is None

    _write_results(tmp_path, 'rq1,"q",m,0.5,True,False,0,2,x,docker\n')
    cr = approval_to_change_request(approval_status(tmp_path))
    assert cr is not None and cr.severity == "high" and cr.section == "Results"


# --- profile wiring -----------------------------------------------------------
def test_thorough_profile_blocks_unapproved_balanced_does_not():
    assert stage_c_caps("thorough").unapproved_block is True
    assert stage_c_caps("balanced").unapproved_block is False


def test_env_override_toggles_block(monkeypatch):
    monkeypatch.setenv("RC_STAGEC_BLOCK_UNAPPROVED", "true")
    assert stage_c_caps("balanced").unapproved_block is True


# --- loop integration (mirrors the claims-gate loop tests) --------------------
_C2 = StageCCaps(max_revisions=2, accept=0.70, usd_budget=0.0)


async def test_zero_approved_does_not_block_accept_by_default(tmp_path):
    # default flag-not-block: a high-scoring paper still accepts, but the count is surfaced and
    # the writer was given the honesty constraint.
    _write_results(tmp_path, 'rq1,"q",m,0.5,True,False,0,2,x,docker\n')
    reviewers = [_Reviewer([0.85], vendor="a"), _Reviewer([0.85], vendor="b")]
    res = await run_writing(
        _handoff(), _FakeWriter(), reviewers, venue="icse", out_dir=tmp_path, caps=_C2, latex=False
    )
    assert res.accepted and res.approved_rqs == 0 and res.total_rqs == 1


async def test_zero_approved_blocks_accept_when_enabled(tmp_path):
    # unapproved_block=True: even a 0.95 paper cannot ship as accepted with 0 RQs approved.
    _write_results(tmp_path, 'rq1,"q",m,0.5,True,False,0,2,x,docker\n')
    caps = StageCCaps(max_revisions=2, accept=0.70, usd_budget=0.0, unapproved_block=True)
    reviewers = [_Reviewer([0.95], vendor="a"), _Reviewer([0.95], vendor="b")]
    res = await run_writing(
        _handoff(), _FakeWriter(), reviewers, venue="icse", out_dir=tmp_path, caps=caps, latex=False
    )
    assert not res.accepted and res.stopped_reason == "revisions_exhausted"
    assert res.approved_rqs == 0 and res.total_rqs == 1


async def test_some_approved_can_still_accept_when_block_enabled(tmp_path):
    # blocking only bites when ZERO are approved; one approval lets a strong paper through.
    _write_results(
        tmp_path,
        'rq1,"q",m,0.5,True,True,2,3,approved,docker\n'
        'rq2,"q2",m,0.6,True,False,0,2,x,docker\n',
    )
    caps = StageCCaps(max_revisions=2, accept=0.70, usd_budget=0.0, unapproved_block=True)
    reviewers = [_Reviewer([0.85], vendor="a"), _Reviewer([0.85], vendor="b")]
    res = await run_writing(
        _handoff(), _FakeWriter(), reviewers, venue="icse", out_dir=tmp_path, caps=caps, latex=False
    )
    assert res.accepted and res.approved_rqs == 1 and res.total_rqs == 2
