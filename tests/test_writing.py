"""Stage C — venue rubric + council writing loop (offline; fake writer/reviewers)."""

from __future__ import annotations

from research_council.debate.caps import StageCCaps
from research_council.debate.writing import list_venues, load_venue, run_writing
from research_council.obs.telemetry import UsageMeter
from research_council.store.models import (
    ChangeRequest,
    Citation,
    PaperDraft,
    ReviewNotes,
    StageHandoff,
)


def test_load_venue_and_fallback():
    icse = load_venue("icse")
    assert "reproducibility" in icse["rubric"] and icse["doc_class"] == "acmart"
    assert load_venue("does-not-exist")["name"] == load_venue("generic")["name"]  # falls back


def test_list_venues_has_the_seven():
    vs = set(list_venues())
    assert {"generic", "icse", "fse", "ase", "neurips", "emnlp", "iclr"} <= vs


class _FakeWriter:
    def __init__(self):
        self.usage = UsageMeter()

    async def draft(
        self, idea, experiment, constraints=None, *, allowed_citations=None, figures=None
    ):
        return PaperDraft(
            title="A Toy Study of X",
            abstract="we did X and measured Y",
            sections={
                "Introduction": "intro text",
                "Method": "method text",
                "Results": f"observed metric {experiment.get('metric')}",
            },
            citations=list(allowed_citations or []),
            figures=list(figures or []),
        )

    async def revise(self, draft, change_requests, sections):
        d = draft.model_copy(deep=True)
        for s in sections:
            d.sections[s] = f"REVISED {s}"
        return d

    async def coherence_pass(self, draft):
        return draft


class _Reviewer:
    """Returns scores from a sequence (one per round); change-requests only on round 1."""

    def __init__(self, scores, *, changes_first=None, vendor="v", verdict="accept", cost=0.0):
        self.usage = UsageMeter(cost_usd=cost)
        self.vendor = vendor
        self._scores = list(scores)
        self._changes_first = changes_first or []
        self._verdict = verdict
        self._n = 0

    async def review(self, draft, rubric):
        s = self._scores[min(self._n, len(self._scores) - 1)]
        ch = self._changes_first if self._n == 0 else []
        self._n += 1
        return ReviewNotes(
            scores={k: s for k in rubric},
            comments=["a comment"],
            change_requests=[c.model_copy(deep=True) for c in ch],
            verdict=self._verdict,
            reviewer_vendor=self.vendor,
        )


def _handoff():
    return StageHandoff(
        from_stage="experimentation",
        to_stage="writing",
        idea={"title": "X", "hypothesis": "h", "method": "m", "experiment_plan": "p"},
        experiment_plan="p",
        constraints={},
        artifacts={"ran": True, "feasible": True, "metric": "f1=0.62", "log": "METRIC f1=0.62"},
    )


_C2 = StageCCaps(max_revisions=2, accept=0.70, usd_budget=0.0)
_C1 = StageCCaps(max_revisions=1, accept=0.70, usd_budget=0.0)


async def test_accepts_on_first_round(tmp_path):
    reviewers = [_Reviewer([0.8], vendor="a"), _Reviewer([0.85], vendor="b")]
    res = await run_writing(
        _handoff(), _FakeWriter(), reviewers, venue="icse", out_dir=tmp_path, caps=_C2, latex=False
    )
    assert res.accepted and res.stopped_reason == "accepted" and res.revisions == 1
    assert res.venue == "icse" and set(res.review.scores)
    paper = (tmp_path / "paper" / "paper.md").read_text()
    assert "A Toy Study of X" in paper and "## Abstract" in paper and "f1=0.62" in paper
    assert paper.index("## Introduction") < paper.index("## Method") < paper.index("## Results")
    assert (tmp_path / "paper" / "sections" / "introduction.md").exists()


async def test_revises_targeted_section_then_accepts(tmp_path):
    cr = ChangeRequest(section="Results", severity="medium", msg="add a baseline")
    reviewers = [
        _Reviewer([0.5, 0.85], changes_first=[cr], vendor="a"),
        _Reviewer([0.5, 0.85], vendor="b"),
    ]
    res = await run_writing(
        _handoff(),
        _FakeWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=_C2,
        latex=False,
    )
    assert res.accepted and res.revisions == 2 and res.score_history[0] < res.score_history[1]
    assert "REVISED Results" in (tmp_path / "paper" / "paper.md").read_text()


async def test_high_severity_blocks_accept_even_with_high_score(tmp_path):
    cr = ChangeRequest(section="Method", severity="high", msg="unsound claim")
    reviewers = [_Reviewer([0.95], changes_first=[cr], vendor="a"), _Reviewer([0.95], vendor="b")]
    res = await run_writing(
        _handoff(),
        _FakeWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=_C1,
        latex=False,
    )
    assert not res.accepted and res.stopped_reason == "revisions_exhausted"


async def test_best_so_far_on_exhaust(tmp_path):
    reviewers = [_Reviewer([0.4, 0.5], vendor="a"), _Reviewer([0.4, 0.5], vendor="b")]
    res = await run_writing(
        _handoff(),
        _FakeWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=_C2,
        latex=False,
    )
    assert not res.accepted and res.stopped_reason == "revisions_exhausted"
    assert res.review.mean >= 0.5  # kept the best-scoring round


async def test_budget_exhausted_stops_writing(tmp_path):
    reviewers = [_Reviewer([0.4], vendor="a", cost=10.0), _Reviewer([0.4], vendor="b")]
    caps = StageCCaps(max_revisions=5, accept=0.70, usd_budget=1.5)
    res = await run_writing(
        _handoff(),
        _FakeWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=caps,
        latex=False,
    )
    assert res.stopped_reason == "budget_exhausted"


async def test_continues_from_prior_draft_without_redrafting(tmp_path):
    from research_council.store.models import PaperDraft

    class _NoDraftWriter(_FakeWriter):
        async def draft(self, *a, **k):
            raise AssertionError("must not redraft when a prior paper exists")

    prior = PaperDraft(
        title="Prior Paper",
        abstract="prior abstract",
        sections={"Introduction": "i", "Method": "m", "Results": "r"},
    )
    reviewers = [_Reviewer([0.85], vendor="a"), _Reviewer([0.85], vendor="b")]
    res = await run_writing(
        _handoff(),
        _NoDraftWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=_C2,
        latex=False,
        prior_draft=prior,
    )
    assert res.title == "Prior Paper" and res.accepted  # improved the existing paper


def test_load_prior_paper_round_trips(tmp_path):
    from research_council.debate.writing import load_prior_paper

    paper = tmp_path / "paper"
    (paper / "sections").mkdir(parents=True)
    (paper / "paper.md").write_text(
        "# My Title\n*meta*\n\n## Abstract\nthe abstract\n\n## Method\nx\n"
    )
    (paper / "sections" / "method.md").write_text("# Method\n\nthe method body\n")
    draft, build_error = load_prior_paper(tmp_path)
    assert draft.title == "My Title" and draft.abstract == "the abstract"
    assert draft.sections.get("Method") == "the method body" and build_error == ""
    assert load_prior_paper(tmp_path / "nope") == (None, "")


async def test_embeds_real_experiment_figures(tmp_path):
    # Stage B left a figure on disk → Stage C copies it into paper/assets and references it
    figdir = tmp_path / "experiment" / "rq1" / "figures"
    figdir.mkdir(parents=True)
    (figdir / "plot.png").write_bytes(b"PNG")
    reviewers = [_Reviewer([0.85], vendor="a"), _Reviewer([0.85], vendor="b")]
    await run_writing(
        _handoff(),
        _FakeWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=_C2,
        latex=False,
    )
    assert (tmp_path / "paper" / "assets" / "rq1_plot.png").read_bytes() == b"PNG"
    paper = (tmp_path / "paper" / "paper.md").read_text()
    assert "## Figures" in paper and "rq1_plot.png" in paper


_RESULTS_CSV = (
    "rq_id,question,metric,value,feasible,approved,approvals,iterations,stopped_reason,backend\n"
    'rq1,"Does it, with commas, work?",interaction_F,5.0812,True,True,2,3,approved,docker\n'
)


class _FabricatingWriter(_FakeWriter):
    """Drafts a Results section with one backed number (5.08) and one fabricated one (0.99).
    On revise, falls back to _FakeWriter (replaces the section with 'REVISED <s>', dropping
    the number) — modelling a writer that removes the unbacked claim when asked."""

    async def draft(self, *a, **k):
        d = await super().draft(*a, **k)
        d.sections["Results"] = "The interaction is significant (F=5.08); efficiency hits 0.99."
        return d


async def test_unbacked_claim_does_not_block_accept_by_default(tmp_path):
    # v1 flag-not-block: a fabricated 0.99 surfaces as a change-request but still accepts.
    (tmp_path / "experiment").mkdir()
    (tmp_path / "experiment" / "results.csv").write_text(_RESULTS_CSV, encoding="utf-8")
    reviewers = [_Reviewer([0.95], vendor="a"), _Reviewer([0.95], vendor="b")]
    res = await run_writing(
        _handoff(),
        _FabricatingWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=_C2,  # claims_unbacked_block defaults False
        latex=False,
    )
    assert res.accepted and res.revisions == 1
    # The post-hoc artifact still records the unbacked claim.
    assert res.claims_unbacked >= 1
    import json

    data = json.loads((tmp_path / "paper" / "claims.json").read_text())
    assert "0.99" in {c["text"] for c in data["unbacked"]}


async def test_unbacked_claim_blocks_accept_and_forces_revision_when_enabled(tmp_path):
    # claims_unbacked_block=True: the fabricated 0.99 blocks acceptance on round 1, forcing a
    # revision; the writer drops the number, and round 2 (no unbacked claims) accepts.
    (tmp_path / "experiment").mkdir()
    (tmp_path / "experiment" / "results.csv").write_text(_RESULTS_CSV, encoding="utf-8")
    caps = StageCCaps(max_revisions=2, accept=0.70, usd_budget=0.0, claims_unbacked_block=True)
    reviewers = [_Reviewer([0.95], vendor="a"), _Reviewer([0.95], vendor="b")]
    res = await run_writing(
        _handoff(),
        _FabricatingWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=caps,
        latex=False,
    )
    assert res.accepted and res.revisions == 2  # blocked round 1, accepted after the fix
    assert "REVISED Results" in (tmp_path / "paper" / "paper.md").read_text()
    assert res.claims_unbacked == 0  # the fabricated number is gone from the final paper


async def test_unbacked_claim_blocking_exhausts_when_writer_wont_fix(tmp_path):
    # If the writer keeps the unbacked number, blocking holds and the loop exhausts revisions
    # rather than shipping a fabricated figure as 'accepted'.
    (tmp_path / "experiment").mkdir()
    (tmp_path / "experiment" / "results.csv").write_text(_RESULTS_CSV, encoding="utf-8")

    class _StubbornWriter(_FabricatingWriter):
        async def revise(self, draft, change_requests, sections):
            return draft.model_copy(deep=True)  # never removes the 0.99

    caps = StageCCaps(max_revisions=2, accept=0.70, usd_budget=0.0, claims_unbacked_block=True)
    reviewers = [_Reviewer([0.95], vendor="a"), _Reviewer([0.95], vendor="b")]
    res = await run_writing(
        _handoff(),
        _StubbornWriter(),
        reviewers,
        venue="generic",
        out_dir=tmp_path,
        caps=caps,
        latex=False,
    )
    assert not res.accepted and res.stopped_reason == "revisions_exhausted"
    assert res.claims_unbacked >= 1


async def test_grounding_filters_unknown_citations_via_testmodel():
    import pytest

    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.writer import Writer

    # an empty allowed-list means the writer may cite nothing — invented keys are dropped
    d = await Writer(TestModel(), venue="ICSE").draft(
        {"title": "X"}, {"metric": "f1=0.5"}, allowed_citations=[]
    )
    assert d.citations == []


async def test_writer_reviewer_offline_via_testmodel():
    import pytest

    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.agents.writer import PaperReviewer, Writer

    real = [Citation(key="smith24", text="Smith et al. 2024")]
    d = await Writer(TestModel(), venue="ICSE").draft(
        {"title": "X"}, {"metric": "f1=0.5"}, allowed_citations=real
    )
    assert isinstance(d, PaperDraft)
    r = await PaperReviewer(TestModel(), venue="ICSE", vendor="openai").review(
        d, {"novelty": "?", "clarity": "?"}
    )
    assert isinstance(r, ReviewNotes) and r.reviewer_vendor == "openai"
