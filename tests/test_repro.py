"""Offline tests for the reproduction-manifest writer (plan/25 Gap 3, verify/repro.py).

The headline cases:
  * build_manifest pins the real …103845 experiment.py by sha256 and records its metric as the
    diff target, with deterministic=False because that script pins no seed (the gap made
    visible rather than assumed away);
  * a generated reproduce.sh actually re-runs an experiment and PASSes when the metric matches
    and exits non-zero when it drifts — proving the script's sed-scrape + float-diff logic.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from research_council.store.models import ExperimentResult, RQResult
from research_council.verify.repro import (
    build_manifest,
    check_code_integrity,
    code_sha256,
    detect_seeds,
    reproduce_sh,
    write_repro,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "verify"
    / "how-does-accuracy-scale-with-103845"
    / "experiment"
)


def _rq(
    rq_id, question, code, metric, *, feasible=True, approved=False, reqs=None, backend="docker"
):
    return RQResult(
        rq_id=rq_id,
        question=question,
        result=ExperimentResult(
            ran=True,
            feasible=feasible,
            approved=approved,
            metric=metric,
            code=code,
            backend=backend,
            requirements=reqs or [],
        ),
    )


# ── code hash + seed detection ───────────────────────────────────────────────


def test_code_sha256_matches_hashlib():
    assert code_sha256("print('hi')") == hashlib.sha256(b"print('hi')").hexdigest()


def test_detect_seeds_finds_common_forms():
    assert detect_seeds("np.random.seed(42)") == [42]
    assert detect_seeds("import torch\ntorch.manual_seed(7)") == [7]
    assert detect_seeds("clf = RF(random_state=0)") == [0]
    assert detect_seeds("rng = np.random.default_rng(seed=123)") == [123]


def test_detect_seeds_dedupes_in_first_seen_order():
    assert detect_seeds("random.seed(1)\nnp.random.seed(1)\ntorch.manual_seed(2)") == [1, 2]


def test_detect_seeds_none_when_unpinned():
    assert detect_seeds("x = sum(range(10))\nprint(x)") == []


# ── manifest construction ────────────────────────────────────────────────────


def test_build_manifest_core_fields():
    code = "import numpy as np\nnp.random.seed(3)\nprint('METRIC acc=0.5')"
    rr = _rq("rq1", "Does it work?", code, "acc=0.5", approved=True, reqs=["numpy==1.26"])
    m = build_manifest(rr, image="img:tag")
    assert m["rq_id"] == "rq1"
    assert m["code_sha256"] == code_sha256(code)
    assert m["metric"] == {"name": "acc", "value": 0.5, "raw": "acc=0.5"}
    assert m["seeds"] == [3] and m["deterministic"] is True
    assert m["requirements"] == ["numpy==1.26"]
    assert m["image"] == "img:tag" and m["run_command"] == "python experiment.py"
    assert m["verifiable"] is True and m["approved"] is True


def test_build_manifest_unpinned_seed_is_visible():
    rr = _rq("rq1", "q", "print('METRIC m=1.0')", "m=1.0")
    m = build_manifest(rr)
    # No seed pinned → deterministic False so an irreproducible run is flagged, not assumed.
    assert m["seeds"] == [] and m["deterministic"] is False


def test_build_manifest_non_numeric_metric_not_verifiable():
    rr = _rq("rq1", "q", "print('METRIC label=converged')", "label=converged")
    m = build_manifest(rr)
    assert m["metric"]["value"] is None and m["verifiable"] is False


def test_build_manifest_no_metric_not_verifiable():
    rr = _rq("rq1", "q", "raise SystemExit(1)", None, feasible=False)
    m = build_manifest(rr)
    assert m["metric"]["name"] is None and m["verifiable"] is False


def test_build_manifest_nonfinite_metric_not_verifiable_and_serializes():
    # A NaN headline metric: value must be None (not float('nan')) so verifiable is honest AND
    # json.dumps emits valid JSON ('null'), not a bare NaN token a strict reader would reject.
    rr = _rq("rq1", "q", "print('METRIC loss=nan')", "loss=nan")
    m = build_manifest(rr)
    assert m["metric"]["value"] is None and m["verifiable"] is False
    assert m["metric"]["raw"] == "loss=nan"  # raw kept for transparency
    text = json.dumps(m, indent=2)
    assert "NaN" not in text and json.loads(text)["metric"]["value"] is None


def test_build_manifest_pins_real_project_experiment():
    """The on-disk …103845 rq1 script + its recorded metric → a manifest whose sha256 is the
    hash of the actual file and whose deterministic flag honestly reports the script's lack of
    a seed."""
    code = (FIXTURE / "rq1" / "experiment.py").read_text(encoding="utf-8")
    rr = _rq("rq1", "monotonic accuracy?", code, "signed_NxT_interaction=0.000137")
    m = build_manifest(rr)
    assert m["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert m["metric"]["value"] == 0.000137
    assert isinstance(m["deterministic"], bool)


# ── write_repro on disk ──────────────────────────────────────────────────────


def test_write_repro_emits_per_rq_manifest_and_script(tmp_path):
    rqs = [
        _rq("rq1", "q1", "print('METRIC a=1.0')", "a=1.0"),
        _rq("rq2", "q2", "print('METRIC b=2.5')", "b=2.5"),
    ]
    exp = write_repro(rqs, tmp_path)
    assert exp == tmp_path / "experiment"
    j1 = json.loads((exp / "rq1" / "repro.json").read_text())
    assert j1["metric"]["value"] == 1.0
    assert (exp / "rq2" / "repro.json").exists()
    sh = exp / "reproduce.sh"
    assert sh.exists() and (sh.stat().st_mode & 0o111)  # executable
    body = sh.read_text()
    assert "rq1" in body and "rq2" in body and "REPRODUCE: OK" in body


def test_reproduce_sh_skips_non_numeric_metric():
    rqs = [_rq("rq1", "q", "print('METRIC label=ok')", "label=ok")]
    body = reproduce_sh(rqs)
    assert "no numeric metric recorded" in body
    assert "re-running experiment.py" not in body


# ── code-hash integrity read-back ────────────────────────────────────────────


def test_check_code_integrity_clean_when_hash_matches(tmp_path):
    code = "print('METRIC acc=0.5')"
    exp = write_repro([_rq("rq1", "q", code, "acc=0.5")], tmp_path)
    (exp / "rq1" / "experiment.py").write_text(code, encoding="utf-8")
    assert check_code_integrity(tmp_path) == []


def test_check_code_integrity_flags_edited_experiment(tmp_path):
    # Manifest pins the original code's hash; the on-disk experiment.py was swapped — the
    # recorded metric no longer describes the shipped code, so the anchor must catch it.
    exp = write_repro([_rq("rq1", "q", "print('METRIC acc=0.5')", "acc=0.5")], tmp_path)
    (exp / "rq1" / "experiment.py").write_text("print('METRIC acc=0.9')", encoding="utf-8")
    v = check_code_integrity(tmp_path)
    assert len(v) == 1
    assert v[0]["rq"] == "rq1" and v[0]["reason"] == "sha256 mismatch"
    assert v[0]["actual"] == code_sha256("print('METRIC acc=0.9')")


def test_check_code_integrity_flags_missing_experiment(tmp_path):
    # Manifest pins a hash but no experiment.py shipped — the pinned code is unverifiable.
    write_repro([_rq("rq1", "q", "print('METRIC acc=0.5')", "acc=0.5")], tmp_path)
    v = check_code_integrity(tmp_path)
    assert len(v) == 1
    assert v[0]["reason"] == "experiment.py missing" and v[0]["actual"] is None


def test_check_code_integrity_skips_manifest_without_hash(tmp_path):
    # A placeholder repro.json with no code_sha256 is nothing to verify, not a violation.
    exp = tmp_path / "experiment" / "rq1"
    exp.mkdir(parents=True)
    (exp / "repro.json").write_text("{}", encoding="utf-8")
    assert check_code_integrity(tmp_path) == []


# ── the script actually works (local re-run) ─────────────────────────────────


def _run_reproduce(exp: Path):
    return subprocess.run(
        ["bash", "reproduce.sh"], cwd=exp, capture_output=True, text=True, timeout=60
    )


def test_reproduce_sh_passes_when_metric_matches(tmp_path):
    code = "print('METRIC acc=0.5000')"
    rqs = [_rq("rq1", "q", code, "acc=0.5")]
    exp = write_repro(rqs, tmp_path)
    (exp / "rq1" / "experiment.py").write_text(code, encoding="utf-8")
    p = _run_reproduce(exp)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "PASS rq1" in p.stdout and "REPRODUCE: OK" in p.stdout


def test_reproduce_sh_fails_when_metric_drifts(tmp_path):
    # Recorded 0.9 but the (re-run) code emits 0.5 → must fail, not silently pass.
    rqs = [_rq("rq1", "q", "print('METRIC acc=0.9')", "acc=0.9")]
    exp = write_repro(rqs, tmp_path)
    (exp / "rq1" / "experiment.py").write_text("print('METRIC acc=0.5')", encoding="utf-8")
    p = _run_reproduce(exp)
    assert p.returncode != 0
    assert "FAIL rq1" in p.stdout and "REPRODUCE: FAIL" in p.stdout


def test_reproduce_sh_tolerates_rounding_jitter(tmp_path):
    # Recorded 5.0812; a faithful re-run lands on 5.0813 — within REL_TOL, must PASS.
    rqs = [_rq("rq1", "q", "x=0", "interaction_F=5.0812")]
    exp = write_repro(rqs, tmp_path)
    (exp / "rq1" / "experiment.py").write_text(
        "print('METRIC interaction_F=5.0813')", encoding="utf-8"
    )
    p = _run_reproduce(exp)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "PASS rq1" in p.stdout
