"""LaTeX build-verify-fix tool for Stage C (plan/18).

"Compiles clean" is the writing analog of Stage B's "ran + emitted METRIC": we scaffold the
accepted paper to .tex, compile it with tectonic (preferred — a single self-contained binary
that fetches packages on demand) or latexmk, and on failure apply mechanical fixes + a
doc-class fallback, recompiling up to `attempts`. References are emitted as an inline
`thebibliography` (NOT bibtex), which structurally avoids the class of bibtex parse errors
(e.g. a stray `@`) that have bitten past sessions.

If no TeX engine is on the machine we still emit paper.tex and return `fallback_no_tex` —
never a hard failure (mirrors verify/sandbox.build_sandbox's refuse-don't-crash posture).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from research_council.store.models import PaperDraft

Emit = Callable[[str, str, dict], None] | None
_ORDER = ["Introduction", "Related Work", "Method", "Experiment", "Results", "Conclusion"]
_SPECIALS = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_engine() -> tuple[str | None, str]:
    """Return (executable, kind) — tectonic preferred, latexmk fallback, else (None, '')."""
    if shutil.which("tectonic"):
        return shutil.which("tectonic"), "tectonic"
    if shutil.which("latexmk"):
        return shutil.which("latexmk"), "latexmk"
    return None, ""


def _esc(text: str) -> str:
    # escape backslash first, then the rest, so we don't double-escape our own commands
    text = text.replace("\\", r"\textbackslash{}")
    for ch, rep in _SPECIALS.items():
        text = text.replace(ch, rep)
    return text


def _body_to_tex(md: str, keys: set[str]) -> str:
    """Convert a section's markdown body to LaTeX: escape specials, lists, citations, images."""
    out: list[str] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        img = re.match(r"!\[[^\]]*\]\(([^)]+)\)", line.strip())
        if img:
            out += [
                r"\begin{figure}[t]",
                r"\centering",
                rf"\includegraphics[width=.7\linewidth]{{{img.group(1)}}}",
                r"\caption{Experiment results.}",
                r"\end{figure}",
            ]
            continue
        if line.strip().startswith(("- ", "* ")):
            out.append(r"\par " + _cite(_esc(line.strip()[2:]), keys))
            continue
        out.append(_cite(_esc(line), keys) if line else "")
    return "\n".join(out)


def _cite(text: str, keys: set[str]) -> str:
    """Turn `[key]` into `\\cite{key}` for known citation keys (after escaping)."""
    for k in keys:
        text = text.replace(_esc(f"[{k}]"), rf"\cite{{{k}}}").replace(f"[{k}]", rf"\cite{{{k}}}")
    return text


def _docclass(doc_class: str) -> str:
    return {
        "acmart": r"\documentclass[sigconf,nonacm]{acmart}",
        "ieeetran": r"\documentclass[conference]{IEEEtran}",
    }.get(doc_class, r"\documentclass{article}")


def scaffold_tex(draft: PaperDraft, venue_cfg: dict, *, doc_class: str | None = None) -> str:
    dc = doc_class or venue_cfg.get("doc_class", "article")
    keys = {c.key for c in draft.citations}
    parts = [
        _docclass(dc),
        r"\usepackage{graphicx}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{hyperref}",
        rf"\title{{{_esc(draft.title or 'Untitled')}}}",
        r"\author{Research Council}",
        r"\begin{document}",
        r"\maketitle",
        r"\begin{abstract}",
        _esc(draft.abstract),
        r"\end{abstract}",
    ]
    for name in [*_ORDER, *[k for k in draft.sections if k not in _ORDER]]:
        if name in draft.sections:
            parts += [
                rf"\section{{{_esc(name)}}}",
                _body_to_tex(draft.sections[name], keys),
            ]
    for i, fig in enumerate(draft.figures, 1):
        parts += [
            r"\begin{figure}[t]",
            r"\centering",
            rf"\includegraphics[width=.7\linewidth]{{{fig}}}",
            rf"\caption{{Figure {i}.}}",
            r"\end{figure}",
        ]
    if draft.citations:
        parts.append(rf"\begin{{thebibliography}}{{{len(draft.citations)}}}")
        for c in draft.citations:
            parts.append(rf"\bibitem{{{c.key}}} {_esc(c.text)}")
        parts.append(r"\end{thebibliography}")
    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


def _compile(engine: str, kind: str, tex_path: Path, timeout: int = 180) -> tuple[bool, str]:
    # cwd = the .tex's dir, and we pass the BARE filename — passing a relative path here while
    # also setting cwd would make latexmk look under <cwd>/<relpath> and miss the file.
    tex_path = tex_path.resolve()
    cwd, name = tex_path.parent, tex_path.name
    if kind == "tectonic":
        cmd = [engine, "--keep-logs", "--outdir", str(cwd), name]
    else:
        cmd = [engine, "-pdf", "-interaction=nonstopmode", "-output-directory=.", name]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except Exception as e:  # pragma: no cover - environment dependent
        return False, str(e)
    pdf = tex_path.with_suffix(".pdf")
    ok = p.returncode == 0 and pdf.exists()
    return ok, (p.stdout + "\n" + p.stderr)[-3000:]


def build_paper_latex(
    draft: PaperDraft, paper_dir: Path, venue_cfg: dict, *, attempts: int = 3, emit: Emit = None
) -> dict:
    """Scaffold → compile → mechanical fix/fallback. Returns {status, pdf, log, tex}."""
    paper_dir = Path(paper_dir)
    paper_dir.mkdir(parents=True, exist_ok=True)
    tex_path = paper_dir / "paper.tex"

    engine, kind = latex_engine()
    # try the venue class first, then plain article as a robust fallback
    classes = [venue_cfg.get("doc_class", "article")]
    if "article" not in classes:
        classes.append("article")

    last_log = ""
    for dc in classes[: max(1, attempts)]:
        tex = scaffold_tex(draft, venue_cfg, doc_class=dc)
        tex_path.write_text(tex, encoding="utf-8")
        if engine is None:
            (paper_dir / "build.log").write_text("no tectonic/latexmk on PATH\n", encoding="utf-8")
            if emit:
                emit("writing", "latex_skip", {"reason": "no_engine"})
            return {"status": "fallback_no_tex", "pdf": "", "log": "", "tex": str(tex_path)}
        ok, log = _compile(engine, kind, tex_path)
        last_log = log
        if emit:
            emit("writing", "latex_compile", {"engine": kind, "doc_class": dc, "ok": ok})
        if ok:
            (paper_dir / "build.log").write_text(log, encoding="utf-8")
            return {
                "status": "built",
                "pdf": str(tex_path.with_suffix(".pdf")),
                "log": log,
                "tex": str(tex_path),
            }

    (paper_dir / "build.log").write_text(last_log, encoding="utf-8")
    return {"status": "build_failed", "pdf": "", "log": last_log, "tex": str(tex_path)}


def compile_existing(paper_dir: Path, *, timeout: int = 180) -> dict:
    """Compile the paper.tex already on disk (used after an LLM latex-fix edits it)."""
    paper_dir = Path(paper_dir)
    tex_path = paper_dir / "paper.tex"
    engine, kind = latex_engine()
    if engine is None or not tex_path.exists():
        status = "fallback_no_tex" if engine is None else "build_failed"
        return {"status": status, "pdf": "", "log": "", "tex": str(tex_path)}
    ok, log = _compile(engine, kind, tex_path, timeout)
    (paper_dir / "build.log").write_text(log, encoding="utf-8")
    if ok:
        return {
            "status": "built",
            "pdf": str(tex_path.with_suffix(".pdf")),
            "log": log,
            "tex": str(tex_path),
        }
    return {"status": "build_failed", "pdf": "", "log": log, "tex": str(tex_path)}
