"""Post-run wiki harvest — offline (fabricated trace events + fake librarian)."""

from __future__ import annotations

from research_council.librarian.harvest import (
    build_internal,
    collect_external,
    harvest_run,
    preview,
)
from research_council.librarian.ingest import Ingestor
from research_council.librarian.schema import WikiPage, parse_page
from research_council.store.models import Paper


def _events():
    return [
        {"run_id": "r1", "kind": "topic", "payload": {"topic": "LLM code review vs SAST"}},
        {"run_id": "r1", "kind": "research_brief",
         "payload": {"codename": "Aiden", "gap": "no class-stratified eval", "refs": ["openalex:W1", "arxiv:2401.1"]}},
        {"run_id": "r1", "kind": "research_brief",
         "payload": {"codename": "Cathy", "gap": "triage cost ignored", "refs": ["openalex:W1", "missing:X"]}},
        {"run_id": "r1", "kind": "candidate",
         "payload": {"id": "Aiden", "title": "Class-stratified eval", "hypothesis": "LLM wins on some classes", "experiment_plan": "Juliet + CodeQL"}},
        {"run_id": "r1", "kind": "discussion_message",
         "payload": {"from_codename": "Cathy", "kind": "critique", "targets": "Aiden", "content": "FP rate?"}},
        {"run_id": "r1", "kind": "recommendation", "payload": {"ranked": ["Aiden"]}},
    ]


def _papers():
    return {
        "openalex:W1": Paper(id="openalex:W1", title="LLMs for vuln detection", abstract="abstract one", source="openalex"),
        "arxiv:2401.1": Paper(id="arxiv:2401.1", title="SAST limits", abstract="abstract two", source="arxiv"),
        # note: "missing:X" cited but NOT in cache → not harvestable
    }


class _FakeLibrarian:
    async def route(self, source, *, updated=None):
        # one page per source, echoing its origin (so we can assert internal vs external)
        t = "findings" if source.origin == "internal" else "papers"
        return [WikiPage(type=t, title=source.title, origin=source.origin,
                         papers=[source.citekey], updated=updated or "2026-06-07", body=source.text[:200])]


def test_collect_external_dedups_caps_and_drops_uncached():
    ext, skipped = collect_external(_events(), _papers(), cap=8)
    keys = [s.citekey for s in ext]
    assert keys == ["openalex:W1", "arxiv:2401.1"]      # deduped; "missing:X" dropped (not in cache)
    assert all(s.origin == "external" and s.text for s in ext)
    capped, skipped2 = collect_external(_events(), _papers(), cap=1)
    assert len(capped) == 1 and skipped2 == 1            # cap respected, overflow counted


def test_build_internal_synthesizes_findings():
    src = build_internal(_events(), "LLM code review vs SAST", "r1")
    assert src is not None and src.origin == "internal" and src.citekey == "council:r1"
    assert "Selected direction" in src.text and "Class-stratified eval" in src.text
    assert "no class-stratified eval" in src.text  # a surfaced gap
    # no synthesis when the run had no briefs/candidates
    assert build_internal([{"run_id": "x", "kind": "topic", "payload": {"topic": "t"}}], "t", "x") is None


def test_preview_counts():
    n_ext, has_internal, skipped = preview(_events(), _papers(), cap=8)
    assert n_ext == 2 and has_internal is True and skipped == 0


async def test_harvest_run_writes_internal_and_external(tmp_path):
    ing = Ingestor(_FakeLibrarian(), knowledge_root=tmp_path)
    rep = await harvest_run(_events(), _papers(), ing, cap=8)

    assert set(rep.external) == {"openalex:W1", "arxiv:2401.1"}
    assert rep.internal  # at least one internal page written
    # external papers preserved as immutable raw sources
    assert (tmp_path / "raw/external/openalex-W1.md").exists()
    # the council synthesis is a wiki page tagged internal (never prior art)
    findings = list((tmp_path / "wiki/findings").glob("*.md"))
    assert findings and parse_page(findings[0].read_text()).origin == "internal"
    # index + log updated
    assert "## papers" in (tmp_path / "wiki/index.md").read_text()
    assert "council findings" in (tmp_path / "wiki/log.md").read_text().lower()
