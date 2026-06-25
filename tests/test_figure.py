"""Offline tests for the results-figure metric parsing (verify/figure.py).

These cover the value layer only (no matplotlib needed): a non-finite metric (NaN/±inf) is a
numerically-broken result that cannot be drawn as a bar height, so it must never reach the
chart. `_num` drops it and `_metrics` therefore yields no broken entry for it.
"""

from __future__ import annotations

from research_council.verify.figure import _metrics, _num


def test_num_parses_finite_floats():
    assert _num("0.9") == 0.9
    assert _num("-3") == -3.0


def test_num_rejects_non_numeric():
    assert _num("converged") is None
    assert _num("") is None


def test_num_rejects_non_finite():
    # float('nan')/('inf') parse as floats but can't be a bar height — dropped, not plotted.
    assert _num("nan") is None
    assert _num("inf") is None
    assert _num("-inf") is None
    assert _num("NaN") is None


def test_metrics_excludes_nonfinite_rq_metric():
    # rq1 is a usable number; rq2's metric is NaN → only rq1 becomes a chart entry.
    experiment = {
        "rqs": [
            {"rq_id": "rq1", "result": {"metric": "acc=0.8"}},
            {"rq_id": "rq2", "result": {"metric": "loss=nan"}},
        ]
    }
    assert _metrics(experiment) == [("rq1", 0.8)]


def test_metrics_excludes_nonfinite_from_log_fallback():
    experiment = {"metric": "", "log": "METRIC f1=0.5\nMETRIC bad=inf\n"}
    assert _metrics(experiment) == [("f1", 0.5)]
