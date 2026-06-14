"""Citation-to-record resolver + references.bib emitter (plan/25 Gap 2) — offline, no network.

The gap: citations are filtered to the LLM-wiki allow-list (so keys can't be invented) but the
wiki pages are model-generated — "grounded" means "from our corpus", not "exists in the
literature". A reader gets bare titles, no DOI. This suite proves the resolver attaches a real
DOI/record when one matches, keeps-but-flags a citation that doesn't, and always emits the
.bib artifact — using FAKE providers so it runs with no API keys."""

from __future__ import annotations

import json

from test_writing import _FakeWriter, _handoff, _Reviewer  # reuse the loop harness

from research_council.debate.caps import StageCCaps
from research_council.debate.writing import run_writing
from research_council.store.models import Citation, Paper, PaperDraft
from research_council.verify.bib import (
    MATCH_THRESHOLD,
    BibReport,
    Resolution,
    bib_to_change_requests,
    resolve_citation,
    resolve_citations,
    title_similarity,
    to_bibtex,
    write_bib,
)


class _FakeProvider:
    """Returns a fixed paper list regardless of query (or raises, to test degradation)."""

    def __init__(self, papers, *, name="fake", boom=False):
        self.name = name
        self._papers = papers
        self._boom = boom

    async def search(self, query, k=10):
        if self._boom:
            raise RuntimeError("provider down")
        return list(self._papers)[:k]


def _paper(title, **kw):
    return Paper(id=kw.pop("id", "p1"), title=title, source=kw.pop("source", "openalex"), **kw)


# --- title similarity ---------------------------------------------------------
def test_similarity_identical_is_one():
    assert title_similarity("Neural Program Repair", "Neural Program Repair") == 1.0


def test_similarity_tolerates_paraphrase_and_punctuation():
    s = title_similarity(
        "LLM-based Automated Program Repair", "LLM based Automated Program Repair!"
    )
    assert s >= MATCH_THRESHOLD


def test_similarity_subtitle_drift_via_jaccard():
    # a dropped subtitle: token overlap is high so Jaccard keeps it above threshold
    s = title_similarity(
        "Automated Program Repair with Large Language Models",
        "Automated Program Repair with Large Language Models: An Empirical Study",
    )
    assert s >= MATCH_THRESHOLD


def test_similarity_rejects_unrelated():
    assert title_similarity("Neural Program Repair", "A Survey of Database Indexing") < 0.5


def test_similarity_empty_is_zero():
    assert title_similarity("", "anything") == 0.0


# --- resolve_citation ---------------------------------------------------------
async def test_resolves_strong_title_match_with_doi():
    cit = Citation(key="repair", text="Neural Program Repair")
    prov = _FakeProvider(
        [_paper("Neural Program Repair", year=2021, url="https://doi.org/10.1145/1234567.890")]
    )
    res = await resolve_citation(cit, [prov])
    assert res.resolved and res.doi == "10.1145/1234567.890" and res.year == 2021
    assert res.source == "openalex" and res.score >= MATCH_THRESHOLD


async def test_unrelated_results_leave_it_unresolved():
    cit = Citation(key="repair", text="Neural Program Repair")
    prov = _FakeProvider([_paper("Cooking with Cast Iron", url="https://doi.org/10.1/x")])
    res = await resolve_citation(cit, [prov])
    assert not res.resolved and res.doi == "" and res.matched_title == ""


async def test_no_providers_is_unresolved_not_crash():
    res = await resolve_citation(Citation(key="k", text="t"), [])
    assert not res.resolved and res.key == "k"


async def test_dead_provider_is_skipped():
    cit = Citation(key="repair", text="Neural Program Repair")
    good = _FakeProvider([_paper("Neural Program Repair", url="https://doi.org/10.1/ok")])
    res = await resolve_citation(cit, [_FakeProvider([], boom=True), good])
    assert res.resolved  # the dead source degraded, the live one still matched


async def test_picks_best_scoring_across_providers():
    cit = Citation(key="repair", text="Neural Program Repair")
    weak = _FakeProvider([_paper("Neural Repair Programs", source="arxiv")], name="arxiv")
    strong = _FakeProvider([_paper("Neural Program Repair", source="openalex")], name="openalex")
    res = await resolve_citation(cit, [weak, strong])
    assert res.resolved and res.source == "openalex"


async def test_arxiv_url_without_doi_still_resolves_with_url():
    cit = Citation(key="x", text="Scaling Laws for Code")
    prov = _FakeProvider(
        [_paper("Scaling Laws for Code", url="https://arxiv.org/abs/2401.00001", source="arxiv")]
    )
    res = await resolve_citation(cit, [prov])
    assert res.resolved and res.doi == "" and res.url.endswith("2401.00001")


# --- to_bibtex (pure) ---------------------------------------------------------
def test_bibtex_resolved_entry_has_doi_year_and_is_article():
    cit = Citation(key="repair", text="Neural Program Repair")
    res = Resolution(
        key="repair", query_title="Neural Program Repair", resolved=True, doi="10.1145/1.2",
        url="https://doi.org/10.1145/1.2", year=2021, source="openalex", score=1.0,
    )
    bib = to_bibtex([cit], [res])
    assert "@article{repair," in bib and "doi = {10.1145/1.2}" in bib and "year = {2021}" in bib
    assert "UNVERIFIED: no matching record found" not in bib  # the per-entry tag, not the header


def test_bibtex_unresolved_entry_is_tagged_unverified():
    cit = Citation(key="ghost", text="A Paper That May Not Exist")
    bib = to_bibtex([cit], [Resolution(key="ghost", query_title="A Paper That May Not Exist")])
    assert "@misc{ghost," in bib and "UNVERIFIED: no matching record found" in bib


def test_bibtex_missing_resolution_defaults_unverified():
    # a citation with no Resolution at all still gets an entry (never silently dropped)
    bib = to_bibtex([Citation(key="k", text="Title")], resolutions=[])
    assert "@misc{k," in bib and "UNVERIFIED" in bib


def test_bibtex_escapes_special_chars():
    cit = Citation(key="amp", text="Cost & Latency of 50% Models")
    bib = to_bibtex([cit], [])
    assert r"\&" in bib and r"\%" in bib


def test_bibtex_empty_is_header_only():
    bib = to_bibtex([], [])
    assert bib.startswith("% references.bib") and "@" not in bib


# --- write_bib ----------------------------------------------------------------
async def test_write_bib_emits_artifacts_and_counts(tmp_path):
    draft = PaperDraft(
        title="T",
        citations=[
            Citation(key="real", text="Neural Program Repair"),
            Citation(key="ghost", text="An Imaginary Unmatchable Paper"),
        ],
    )
    prov = _FakeProvider([_paper("Neural Program Repair", url="https://doi.org/10.1145/3597926")])
    report = await write_bib(tmp_path, draft, [prov])
    assert report.n_total == 2 and report.n_resolved == 1 and report.n_unresolved == 1
    bib = (tmp_path / "paper" / "references.bib").read_text()
    assert "@article{real," in bib and "@misc{ghost," in bib
    j = json.loads((tmp_path / "paper" / "references.json").read_text())
    assert j["n_resolved"] == 1 and len(j["resolutions"]) == 2


async def test_write_bib_offline_emits_all_unverified(tmp_path):
    draft = PaperDraft(title="T", citations=[Citation(key="a", text="Some Title")])
    report = await write_bib(tmp_path, draft, providers=None)
    assert report.n_resolved == 0 and report.n_total == 1
    assert "UNVERIFIED" in (tmp_path / "paper" / "references.bib").read_text()


async def test_write_bib_zero_citations_still_writes_header(tmp_path):
    report = await write_bib(tmp_path, PaperDraft(title="T", citations=[]), [])
    assert report.n_total == 0
    assert (tmp_path / "paper" / "references.bib").exists()


# --- change-requests ----------------------------------------------------------
def test_change_requests_only_for_unresolved():
    report = BibReport(
        resolutions=[
            Resolution(key="ok", query_title="X", resolved=True),
            Resolution(key="bad", query_title="Y", resolved=False),
        ]
    )
    crs = bib_to_change_requests(report)
    assert len(crs) == 1 and "[bad]" in crs[0].msg and crs[0].severity == "low"


# --- loop integration ---------------------------------------------------------
async def test_run_writing_emits_references_and_sets_counts(tmp_path):
    cites = [Citation(key="repair", text="Neural Program Repair")]
    prov = _FakeProvider([_paper("Neural Program Repair", url="https://doi.org/10.1145/3597926")])
    reviewers = [_Reviewer([0.85], vendor="a"), _Reviewer([0.85], vendor="b")]
    res = await run_writing(
        _handoff(),
        _FakeWriter(),
        reviewers,
        venue="icse",
        out_dir=tmp_path,
        caps=StageCCaps(max_revisions=1, accept=0.70, usd_budget=0.0),
        allowed_citations=cites,
        bib_providers=[prov],
        latex=False,
    )
    assert res.refs_total == 1 and res.refs_resolved == 1
    assert (tmp_path / "paper" / "references.bib").read_text().count("@article{repair,") == 1


async def test_resolve_citations_batches(tmp_path):
    cites = [Citation(key="a", text="Neural Program Repair"), Citation(key="b", text="Nope Nope")]
    prov = _FakeProvider([_paper("Neural Program Repair", url="https://doi.org/10.1145/3597926")])
    out = await resolve_citations(cites, [prov])
    assert [r.resolved for r in out] == [True, False]
