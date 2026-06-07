"""Librarian schema + router — offline (schema is pure; router via PydanticAI TestModel)."""

from __future__ import annotations

import pytest

from research_council.librarian.schema import (
    TAXONOMY,
    Source,
    WikiPage,
    parse_page,
    render_page,
    slugify,
    wiki_path,
)


def test_slugify():
    assert slugify("Agentic Program Repair!") == "agentic-program-repair"
    assert slugify("   ") == "untitled"


def test_page_roundtrips_through_frontmatter():
    p = WikiPage(type="approaches", title="Agentic Repair w/ Execution Feedback",
                 origin="external", papers=["openalex:W1"], related=["[[concepts:tests-as-oracle]]"],
                 updated="2026-06-07", body="The technique builds X.\n\n## Detail\n- a")
    again = parse_page(render_page(p))
    assert again.type == "approaches" and again.origin == "external"
    assert again.slug == "agentic-repair-w-execution-feedback"  # derived from title
    assert again.papers == ["openalex:W1"] and again.related == ["[[concepts:tests-as-oracle]]"]
    assert "## Detail" in again.body


def test_origin_guard_and_type_validation():
    # invalid origin coerces to external (safe default for the novelty guard)
    assert WikiPage(type="gaps", title="g", origin="bogus").origin == "external"
    # internal origin is preserved (council synthesis — excluded from prior art)
    assert WikiPage(type="findings", title="f", origin="internal").origin == "internal"
    with pytest.raises(ValueError):
        WikiPage(type="not-a-folder", title="x")


def test_wiki_path():
    p = WikiPage(type="benchmarks", title="Juliet Test Suite")
    assert wiki_path("knowledge", p).as_posix().endswith("knowledge/wiki/benchmarks/juliet-test-suite.md")


async def test_router_routes_source_into_typed_pages():
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.librarian.router import Librarian

    args = {"pages": [
        {"type": "papers", "title": "Doe 2025: Agentic Repair", "body": "anchor",
         "related": ["[[approaches:agentic-repair]]"]},
        {"type": "approaches", "title": "Agentic Repair", "body": "the technique"},
        {"type": "bogus-folder", "title": "dropped", "body": "x"},   # invalid type → dropped
        {"type": "gaps", "title": "  ", "body": "y"},                # blank title → dropped
    ]}
    lib = Librarian(TestModel(custom_output_args=args), price_model="claude-sonnet-4-6")
    src = Source(citekey="openalex:W1", title="Agentic repair", text="abstract", origin="external")
    pages = await lib.route(src, updated="2026-06-07")

    assert [p.type for p in pages] == ["papers", "approaches"]  # both malformed routes dropped
    assert all(p.papers == ["openalex:W1"] and p.origin == "external" for p in pages)
    assert all(p.updated == "2026-06-07" and p.type in TAXONOMY for p in pages)
    assert lib.usage.requests > 0 and lib.usage.cost_usd > 0  # costed


class _FakeLibrarian:
    """Returns preset pages (decouples ingest tests from the LLM router)."""

    def __init__(self, spec: list[tuple[str, str, str]]):
        self.spec = spec  # (type, title, body)

    async def route(self, source, *, updated=None):
        return [WikiPage(type=t, title=ti, origin=source.origin, papers=[source.citekey],
                         updated=updated, body=b) for t, ti, b in self.spec]


async def test_ingest_writes_pages_index_log_and_raw(tmp_path):
    from research_council.librarian.ingest import Ingestor

    fake = _FakeLibrarian([
        ("papers", "Doe 2025 Repair", "anchor note"),
        ("approaches", "Agentic Repair", "the technique"),
        ("gaps", "No class-stratified eval", "open problem"),
    ])
    ing = Ingestor(fake, knowledge_root=tmp_path)
    src = Source(citekey="openalex:W1", title="Agentic repair", text="abstract text",
                 origin="external", url="http://x")
    rep = await ing.ingest(src, updated="2026-06-07")

    assert len(rep.written) == 3 and not rep.merged
    assert (tmp_path / "wiki/papers/doe-2025-repair.md").exists()
    assert (tmp_path / "wiki/approaches/agentic-repair.md").exists()
    # external source gets an immutable raw copy
    assert rep.raw_saved and (tmp_path / "raw/external/openalex-W1.md").exists()
    # index.md catalogs by type; log.md records the ingest
    idx = (tmp_path / "wiki/index.md").read_text()
    assert "## approaches" in idx and "[Agentic Repair](approaches/agentic-repair.md)" in idx
    assert "ingest | Agentic repair (external)" in (tmp_path / "wiki/log.md").read_text()
    # the papers anchor cross-links to its fan-out
    anchor = (tmp_path / "wiki/papers/doe-2025-repair.md").read_text()
    assert "[[approaches:agentic-repair]]" in anchor and "[[gaps:no-class-stratified-eval]]" in anchor


async def test_ingest_merges_and_unions_provenance(tmp_path):
    from research_council.librarian.ingest import Ingestor

    await Ingestor(_FakeLibrarian([("concepts", "Tests as Oracle", "idea from p1")]),
                   knowledge_root=tmp_path).ingest(
        Source(citekey="s1", title="P1", text="a", origin="external"), updated="2026-06-07")
    rep = await Ingestor(_FakeLibrarian([("concepts", "Tests as Oracle", "idea from p2")]),
                         knowledge_root=tmp_path).ingest(
        Source(citekey="s2", title="P2", text="b", origin="external"), updated="2026-06-08")

    assert rep.merged == ["concepts/tests-as-oracle.md"] and not rep.written
    page = (tmp_path / "wiki/concepts/tests-as-oracle.md").read_text()
    assert "idea from p1" in page and "idea from p2" in page  # compounded, not overwritten
    assert set(parse_page(page).papers) == {"s1", "s2"}        # provenance unioned


async def test_internal_origin_skips_raw_then_escalates_on_external_merge(tmp_path):
    from research_council.librarian.ingest import Ingestor

    rep = await Ingestor(_FakeLibrarian([("findings", "Result X", "council synthesis")]),
                         knowledge_root=tmp_path).ingest(
        Source(citekey="council:r1", title="deliberation", text="...", origin="internal"),
        updated="2026-06-07")
    assert rep.raw_saved is None and not (tmp_path / "raw/external").exists()
    assert parse_page((tmp_path / "wiki/findings/result-x.md").read_text()).origin == "internal"

    # an external source later touching the same page escalates it to prior art (external)
    await Ingestor(_FakeLibrarian([("findings", "Result X", "from a real paper")]),
                   knowledge_root=tmp_path).ingest(
        Source(citekey="openalex:W9", title="real", text="x", origin="external"), updated="2026-06-08")
    assert parse_page((tmp_path / "wiki/findings/result-x.md").read_text()).origin == "external"


async def test_router_propagates_internal_origin():
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    from research_council.librarian.router import Librarian

    args = {"pages": [{"type": "findings", "title": "Council noted X", "body": "synthesis"}]}
    lib = Librarian(TestModel(custom_output_args=args))
    src = Source(citekey="council:run-1", title="deliberation", text="...", origin="internal")
    pages = await lib.route(src)
    assert pages and pages[0].origin == "internal"  # never counted as prior art
