"""Reproduction-manifest writer (plan/25 Gap 3).

"Verifiable" has two halves. Gap 1 (verify/claims.py) checks that the paper's numbers
*follow from* the data file. This module addresses the other half: that the data file can
itself be *re-generated*. As shipped today a Stage-B experiment leaves behind its code and a
scalar ``METRIC name=value`` but **no re-run recipe** — no seed, no pinned deps, no hash of
the code that produced the number, no command to reproduce it. A reader cannot tell whether
``interaction_F=5.0812`` is reproducible or a one-off; the artifact chain is open.

This module closes that gap *offline* (no sandbox, no API keys): from each RQ's
``ExperimentResult`` it builds a per-RQ ``repro.json`` manifest — sha256 of the exact
``experiment.py``, the declared requirements, the captured metric (the diff target), any
random seed detectable in the code, the sandbox image, and the exact run command — and a
top-level ``reproduce.sh`` that re-runs every RQ and diffs the freshly-emitted METRIC against
the recorded value, exiting non-zero on any mismatch.

Design decisions:
  * The manifest is built from artifacts already in hand at ``write_experiments`` time, so it
    needs no live sandbox and is fully offline-testable against the on-disk fixtures.
  * The code hash is the anchor: it lets a verifier confirm the script that ran is the script
    shipped. A mismatching sha256 means the recorded metric no longer describes this code.
  * Seeds are *detected*, not injected — we don't rewrite the council's code. We surface
    whether the experiment pinned a seed (``deterministic``) so an irreproducible run is
    visible rather than silently assumed repeatable.
  * ``reproduce.sh`` tolerates rounding the same way claims.py does (relative tolerance) so a
    genuine re-run that lands on 5.0813 vs 5.0812 is not reported as a regression.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from research_council.store.models import RQResult

# Common ways generated experiment code pins randomness. We record what we find so a missing
# seed is *visible* (deterministic=False), not silently assumed reproducible.
_SEED_PATTERNS = [
    re.compile(r"np(?:\.random)?\.random\.seed\s*\(\s*(\d+)\s*\)"),
    re.compile(r"numpy\.random\.seed\s*\(\s*(\d+)\s*\)"),
    re.compile(r"\brandom\.seed\s*\(\s*(\d+)\s*\)"),
    re.compile(r"torch\.manual_seed\s*\(\s*(\d+)\s*\)"),
    re.compile(r"tf\.random\.set_seed\s*\(\s*(\d+)\s*\)"),
    re.compile(r"\bset_seed\s*\(\s*(\d+)\s*\)"),
    re.compile(r"\bseed\s*=\s*(\d+)"),
    re.compile(r"random_state\s*=\s*(\d+)"),
]

# Default sandbox image (kept in sync with verify/sandbox.EXPERIMENT_IMAGE). Imported lazily so
# this module stays offline — referencing the constant must never shell out to docker.
DEFAULT_IMAGE = "research-council-exp:latest"

# Re-run is a match if |fresh - recorded| <= REL_TOL * |recorded| (or both ~0). Mirrors the
# rounding tolerance in claims.py so honest float jitter on re-run isn't flagged as a drift.
REL_TOL = 0.02


def code_sha256(code: str) -> str:
    """sha256 of the experiment source — the anchor that ties a recorded metric to its code."""
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def detect_seeds(code: str) -> list[int]:
    """Random seeds pinned in the code (deduped, in first-seen order). Heuristic by design —
    we report what is pinned so a non-deterministic experiment is visible, not hidden."""
    seen: list[int] = []
    for pat in _SEED_PATTERNS:
        for m in pat.finditer(code or ""):
            v = int(m.group(1))
            if v not in seen:
                seen.append(v)
    return seen


def _metric_parts(metric: str | None) -> tuple[str | None, float | None]:
    """('name', value) from a 'name=value' METRIC string, value parsed to float if numeric."""
    if not metric or "=" not in metric:
        return (None, None)
    name, _, raw = metric.partition("=")
    try:
        return (name.strip(), float(raw.strip()))
    except ValueError:
        return (name.strip(), None)


def build_manifest(rr: RQResult, *, image: str = DEFAULT_IMAGE) -> dict:
    """The repro.json content for one RQ — everything a reader needs to re-run it and check
    the result, derived purely from the artifacts already produced by Stage B."""
    r = rr.result
    name, value = _metric_parts(r.metric)
    seeds = detect_seeds(r.code or "")
    return {
        "rq_id": rr.rq_id,
        "question": rr.question,
        "code_file": "experiment.py",
        "code_sha256": code_sha256(r.code or ""),
        "requirements": list(r.requirements),
        "metric": {"name": name, "value": value, "raw": r.metric},
        "seeds": seeds,
        "deterministic": bool(seeds),
        "image": image,
        "backend": r.backend,
        "run_command": "python experiment.py",
        "feasible": r.feasible,
        "approved": r.approved,
        # A metric that isn't a finite number can't be diffed on re-run.
        "verifiable": bool(r.feasible and value is not None),
    }


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def reproduce_sh(rq_results: list[RQResult], *, rel_tol: float = REL_TOL) -> str:
    """A POSIX shell script that re-runs each RQ's experiment.py, re-extracts its METRIC, and
    diffs against the recorded value within ``rel_tol``. Exits non-zero if any RQ regresses or
    fails to re-emit its metric — so a broken artifact chain is a failed command, not prose."""
    lines = [
        "#!/usr/bin/env bash",
        "# Auto-generated by research_council (plan/25 Gap 3). Re-runs every RQ experiment and",
        "# diffs its metric against the value recorded at council time. Exit 0 = all reproduce.",
        "set -u",
        'cd "$(dirname "$0")"',
        f"REL_TOL={rel_tol}",
        "fail=0",
        "",
    ]
    for rr in rq_results:
        name, value = _metric_parts(rr.result.metric)
        rq = rr.rq_id
        if value is None:
            lines += [
                f"echo '== {rq}: no numeric metric recorded — skipping diff =='",
                "",
            ]
            continue
        lines += [
            f"echo '== {rq}: re-running experiment.py =='",
            f"( cd {_sh_quote(rq)} \\",
            "  && { [ -f requirements.txt ] && pip install -q -r requirements.txt || true; } \\",
            "  && out=$(python experiment.py 2>&1) \\",
            '  && echo "$out" \\',
            "  && got=$(echo \"$out\" | sed -n 's/.*METRIC[[:space:]]\\{1,\\}"
            + _re_sed(name)
            + "[[:space:]]*=[[:space:]]*\\([^[:space:]]*\\).*/\\1/p' | tail -n1) \\",
            f"  && python3 -c \"import sys; got=float('$got'); exp={value!r}; rel={rel_tol!r}; "
            "sys.exit(0 if abs(got-exp) <= rel*abs(exp) or (got==0 and exp==0) else 1)\" \\",
            f"  && echo 'PASS {rq}: '\"$got\"' ~= {value}' \\",
            f"  || {{ echo 'FAIL {rq}: expected {value}, got '\"${{got:-<none>}}\"; exit 1; }} ) "
            "|| fail=1",
            "",
        ]
    lines += [
        'if [ "$fail" -ne 0 ]; then echo "REPRODUCE: FAIL"; exit 1; fi',
        'echo "REPRODUCE: OK"',
        "",
    ]
    return "\n".join(lines)


def _re_sed(metric_name: str | None) -> str:
    """Escape a metric name for use inside the sed BRE that scrapes its METRIC line."""
    if not metric_name:
        return "[^[:space:]=]\\{1,\\}"
    # Escape BRE specials so a metric like 'acc.1' or 'f[x]' is matched literally.
    return re.sub(r'([.[\]*^$\\/])', r'\\\1', metric_name)


def write_repro(
    rq_results: list[RQResult], out_dir: Path | str, *, image: str = DEFAULT_IMAGE
) -> Path:
    """Materialize per-RQ ``experiment/<rq>/repro.json`` plus an executable top-level
    ``experiment/reproduce.sh``. Returns the experiment dir. Offline — no sandbox needed."""
    exp = Path(out_dir) / "experiment"
    exp.mkdir(parents=True, exist_ok=True)
    for rr in rq_results:
        sub = exp / rr.rq_id
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "repro.json").write_text(
            json.dumps(build_manifest(rr, image=image), indent=2) + "\n", encoding="utf-8"
        )
    sh = exp / "reproduce.sh"
    sh.write_text(reproduce_sh(rq_results), encoding="utf-8")
    sh.chmod(0o755)
    return exp
