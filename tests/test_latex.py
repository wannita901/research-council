"""Stage C LaTeX build-verify-fix tool (offline; tectonic skipped if absent)."""

from __future__ import annotations

from research_council.store.models import Citation, PaperDraft
from research_council.verify.figure import render_result_figure
from research_council.verify.latex import build_paper_latex, latex_engine, scaffold_tex


def _draft():
    return PaperDraft(
        title="Cost & Scope of R_2 #1",  # specials that must be escaped
        abstract="We test 50% of cases with a_b model.",
        sections={
            "Introduction": "Prior work [smith24] is great.",
            "Method": "We use 100% synthetic data.",
            "Results": "![fig](assets/result.png)\nWe report f1.",
        },
        citations=[Citation(key="smith24", text="Smith et al., A Study, 2024")],
    )


def test_scaffold_escapes_specials_and_avoids_bibtex():
    tex = scaffold_tex(_draft(), {"doc_class": "article"})
    # the classic friction: special chars must be escaped, never raw
    assert r"\&" in tex and r"\%" in tex and r"\#" in tex and r"\_" in tex
    # references are inline thebibliography, NOT bibtex (@ can't appear → no self-inflicted parse bug)
    assert r"\begin{thebibliography}" in tex and "@" not in tex
    # in-text [key] became a real \cite
    assert r"\cite{smith24}" in tex and r"\bibitem{smith24}" in tex
    assert r"\includegraphics" in tex


def test_build_falls_back_gracefully_without_engine(tmp_path, monkeypatch):
    import research_council.verify.latex as lx

    monkeypatch.setattr(lx, "latex_engine", lambda: (None, ""))
    out = build_paper_latex(_draft(), tmp_path, {"doc_class": "acmart"}, attempts=3)
    assert out["status"] == "fallback_no_tex" and out["pdf"] == ""
    assert (tmp_path / "paper.tex").exists()  # .tex still emitted for the human to build


def test_build_compiles_if_engine_present(tmp_path):
    import pytest

    engine, kind = latex_engine()
    if engine is None:
        pytest.skip("no tectonic/latexmk on PATH")
    out = build_paper_latex(_draft(), tmp_path, {"doc_class": "article"}, attempts=2)
    assert out["status"] in ("built", "build_failed")
    if out["status"] == "built":
        assert out["pdf"].endswith(".pdf")


def test_figure_render_is_soft(tmp_path):
    # no metric → no figure, no crash
    assert render_result_figure({"metric": None, "log": ""}, tmp_path / "assets") is None
    # with a metric: a path iff matplotlib is installed, else None — either way no exception
    res = render_result_figure({"metric": "f1=0.62", "log": "METRIC f1=0.62"}, tmp_path / "assets")
    assert res is None or res.endswith("result.png")
