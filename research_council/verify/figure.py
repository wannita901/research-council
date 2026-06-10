"""Results-figure generation for Stage C (plan/18).

The figure is built from the experiment's real METRIC line(s) by TRUSTED host code (not
LLM-generated code), so it doesn't need the sandbox. matplotlib is optional: if it isn't
installed we return None and the paper simply omits the figure (the writer is told there
isn't one). This keeps the dependency soft and the loop honest.
"""

from __future__ import annotations

import re
from pathlib import Path

_METRIC = re.compile(r"METRIC\s+([^\s=]+)\s*=\s*(\S+)")


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _metrics(experiment: dict) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    # RQ-driven results (plan/21): one labelled bar per research question
    for rr in experiment.get("rqs") or []:
        metric = (rr.get("result") or {}).get("metric") or ""
        _, _, v = metric.partition("=")
        f = _num(v)
        if f is not None:
            out.append((rr.get("rq_id") or metric.split("=")[0], f))
    if out:
        return out
    # single-experiment fallback: parse METRIC lines from metric/log
    text = f"{experiment.get('metric') or ''}\n{experiment.get('log') or ''}"
    seen = set()
    for name, val in _METRIC.findall(text):
        f = _num(val)
        if f is not None and name not in seen:
            seen.add(name)
            out.append((name, f))
    if not out and "=" in (experiment.get("metric") or ""):
        n, _, v = experiment["metric"].partition("=")
        f = _num(v)
        if f is not None:
            out.append((n.strip(), f))
    return out


def render_result_figure(experiment: dict, assets_dir: Path) -> str | None:
    """Render a small bar chart of the metric(s) → assets/result.png. Returns the path or None."""
    metrics = _metrics(experiment)
    if not metrics:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None  # matplotlib not available → no figure (soft dependency)

    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    names = [n for n, _ in metrics]
    vals = [v for _, v in metrics]
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar(names, vals, color="#3b6ea5")
    ax.set_ylabel("value")
    ax.set_title("Experiment results")
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:.3g}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    out = assets_dir / "result.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return str(out)
