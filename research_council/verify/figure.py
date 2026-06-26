"""Results-figure generation for Stage C (plan/18).

The figure is built from the experiment's real METRIC line(s) by TRUSTED host code (not
LLM-generated code), so it doesn't need the sandbox. matplotlib is optional: if it isn't
installed we return None and the paper simply omits the figure (the writer is told there
isn't one). This keeps the dependency soft and the loop honest.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

_METRIC = re.compile(r"METRIC\s+([^\s=]+)\s*=\s*(\S+)")


def caption_for_figure(path: str) -> str:
    """A readable caption from the figure's filename — Stage B saves descriptive names like
    ``RQ1_invariant_A_mrr_vs_noise.png``, which carry more than the empty 'Figure N.' the paper
    emitted before. ponytail: derived, not LLM-authored — good enough and free; swap for a
    writer-authored caption if these read too terse."""
    parts = Path(path).stem.split("_")
    prefix = ""
    if parts and re.fullmatch(r"[Rr][Qq]\d+", parts[0]):
        prefix, parts = parts[0].upper() + ": ", parts[1:]
    _stop = {"vs", "of", "to", "at", "on", "in", "by"}
    words = [p.upper() if (len(p) <= 3 and p.isalpha() and p not in _stop) else p for p in parts]
    text = " ".join(words).strip()
    return (prefix + text).strip() or "Experiment results"


# Magic-byte prefixes that identify a structurally-valid raster/vector image by suffix. PNG and
# PDF are binary with a fixed header; SVG is XML text whose root element is <svg ...>. Used by
# is_valid_figure to reject the bad-image cases the existence check misses: a 0-byte file the
# sandbox touched but never wrote, a truncated/garbage save, or an LLM that wrote a stack-trace
# into "plot.png" — none of which are correct evidence and any of which breaks \includegraphics.
_IMAGE_MAGIC: dict[str, bytes] = {".png": b"\x89PNG", ".pdf": b"%PDF"}


def is_valid_figure(path: Path) -> bool:
    """True iff `path` is a non-empty file whose bytes match a real image header for its suffix.

    A trusted host check (no sandbox, no image libs): a missing/empty file fails; a .png/.pdf
    must start with its signature; an .svg must contain an <svg root element in its head; any
    other (non-empty) suffix is accepted rather than over-rejected. The point is to catch the
    broken-image cases — empty, truncated, or not-actually-an-image — that the figures that enter
    the paper as evidence must not be, before they reach the reader or break the LaTeX build."""
    p = Path(path)
    try:
        if p.stat().st_size == 0:
            return False
        head = p.read_bytes()[:4096]
    except OSError:
        return False
    suffix = p.suffix.lower()
    if suffix == ".svg":
        return b"<svg" in head.lower()
    magic = _IMAGE_MAGIC.get(suffix)
    return head.startswith(magic) if magic else True


def _num(s: str) -> float | None:
    """Parse a metric value to a *finite* float, or None. NaN/±inf parse as floats but are
    rejected: a non-finite value can't be drawn as a bar height (NaN renders as a missing/blank
    bar, inf blows up the axis), so it has no place on a results chart — it is dropped here so a
    numerically-broken metric never becomes a misleading figure."""
    try:
        v = float(s)
    except (ValueError, TypeError):
        return None
    return v if math.isfinite(v) else None


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
