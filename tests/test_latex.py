"""Stage C LaTeX build-verify-fix tool (offline; tectonic skipped if absent)."""

from __future__ import annotations

import os

from research_council.store.models import Citation, PaperDraft
from research_council.verify.figure import render_result_figure
from research_council.verify.latex import (
    _compile,
    build_paper_latex,
    latex_engine,
    scaffold_tex,
)


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


def test_figures_get_real_captions_not_double_numbered():
    """Figures carried empty '\\caption{Figure N.}' (which LaTeX double-numbers as 'Figure N:
    Figure N.'). Captions are now derived from the descriptive filename and LaTeX numbers them."""
    from research_council.verify.figure import caption_for_figure

    assert (
        caption_for_figure("assets/RQ1_invariant_A_mrr_vs_noise.png")
        == "RQ1: invariant A MRR vs noise"
    )
    draft = PaperDraft(
        title="T",
        abstract="a",
        sections={"Results": "x"},
        figures=["assets/RQ2_mrr_heatmap.png"],
    )
    tex = scaffold_tex(draft, {"doc_class": "article"})
    assert r"\caption{RQ2: MRR heatmap}" in tex  # real caption, no manual "Figure N."
    assert r"\caption{Figure" not in tex  # no double-numbering


def test_scaffold_embeds_resolved_doi_url_in_bibitem():
    """The compiled PDF's references must carry the resolved DOI/URL (plan/25 Gap 2 follow-up):
    scaffold_tex consumes the bib resolutions so each \\bibitem resolves to a real record, not
    just the standalone references.bib."""
    from research_council.verify.bib import Resolution

    draft = PaperDraft(
        title="T",
        abstract="a",
        sections={"Introduction": "x [doi24] [url24] [bad24]."},
        citations=[
            Citation(key="doi24", text="A DOI paper"),
            Citation(key="url24", text="An arXiv paper"),
            Citation(key="bad24", text="An invented paper"),
        ],
    )
    resolutions = [
        Resolution(key="doi24", query_title="A DOI paper", resolved=True, doi="10.1145/1234.5678"),
        Resolution(
            key="url24",
            query_title="An arXiv paper",
            resolved=True,
            url="https://arxiv.org/abs/2401.00001",
        ),
        Resolution(key="bad24", query_title="An invented paper", resolved=False),
    ]
    tex = scaffold_tex(draft, {"doc_class": "article"}, resolutions=resolutions)
    # resolved DOI → a resolvable doi.org link typeset verbatim with \url (no escaping needed)
    assert r"\url{https://doi.org/10.1145/1234.5678}" in tex
    # resolved url-only (arXiv has no DOI) → the record URL
    assert r"\url{https://arxiv.org/abs/2401.00001}" in tex
    # unresolved → a visible [unverified] marker, not a silent bare title
    assert r"\textit{[unverified]}" in tex
    # the bibtex-avoidance invariant still holds — no stray @ from the resolution fields
    assert "@" not in tex


def test_scaffold_without_resolutions_is_unchanged():
    """Backwards-compat: omitting resolutions leaves the bibliography bare (no anchors, no
    [unverified] markers) so existing callers/output are untouched."""
    tex = scaffold_tex(_draft(), {"doc_class": "article"})
    assert r"\bibitem{smith24}" in tex
    assert r"\url{" not in tex and "[unverified]" not in tex


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


def test_compile_invocation_uses_bare_name_in_resolved_cwd(tmp_path, monkeypatch):
    """Regression guard for the path bug seen in project …103845's build.log
    ("Could not find file 'projects/.../paper/paper.tex'"): the engine must be
    invoked with cwd=the .tex's resolved parent and the BARE filename — never a
    relative/foreign path — so it resolves regardless of the process's cwd.
    Offline: subprocess.run is stubbed, so this runs with or without a TeX engine."""
    import research_council.verify.latex as lx

    tex = tmp_path / "sub" / "paper.tex"
    tex.parent.mkdir(parents=True)
    tex.write_text(r"\documentclass{article}\begin{document}x\end{document}", encoding="utf-8")

    seen = {}

    class _P:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _fake_run(cmd, cwd, capture_output, text, timeout):
        seen["cmd"], seen["cwd"] = cmd, cwd
        # mimic a successful build so _compile reports ok
        tex.with_suffix(".pdf").write_text("%PDF-1.5\n", encoding="utf-8")
        return _P()

    monkeypatch.setattr(lx.subprocess, "run", _fake_run)
    # run from a DIFFERENT cwd than the .tex lives in — this is what broke before
    monkeypatch.chdir(tmp_path)
    engine, kind = "latexmk", "latexmk"
    # pass a RELATIVE path to prove _compile resolves it before splitting cwd/name
    ok, _log = _compile(engine, kind, tex.relative_to(tmp_path))

    assert ok
    # the filename argument must be the bare name, with no directory separator
    assert "paper.tex" in seen["cmd"] and not any("/" in str(a) for a in seen["cmd"][1:])
    # ...and cwd must be the resolved parent of the .tex, not the process cwd
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(tex.parent)


def test_figure_render_is_soft(tmp_path):
    # no metric → no figure, no crash
    assert render_result_figure({"metric": None, "log": ""}, tmp_path / "assets") is None
    # with a metric: a path iff matplotlib is installed, else None — either way no exception
    res = render_result_figure({"metric": "f1=0.62", "log": "METRIC f1=0.62"}, tmp_path / "assets")
    assert res is None or res.endswith("result.png")
