"""Wiki lint (plan/16, plan/9) — audit the wiki's health.

Structural checks are pure & offline (broken cross-links, orphans, index drift, empty
pages). An optional `--semantic` pass asks the librarian model to flag cross-page
contradictions and surface missing gaps. Karpathy: "perform periodic lints to catch stale
or orphaned content"; the run is appended to log.md.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from research_council.librarian.schema import parse_page

_SKIP = {"index.md", "log.md"}
_LINK = re.compile(r"\[\[([a-z]+):([a-z0-9-]+)\]\]")
_INDEX_LINK = re.compile(r"\(([a-z]+/[a-z0-9-]+\.md)\)")


class LintIssue(BaseModel):
    kind: str   # broken_link | orphan | index_drift | empty
    page: str   # rel path (or index.md)
    detail: str = ""


class LintReport(BaseModel):
    pages: int = 0
    issues: list[LintIssue] = Field(default_factory=list)

    def by_kind(self) -> dict[str, list[LintIssue]]:
        out: dict[str, list[LintIssue]] = defaultdict(list)
        for i in self.issues:
            out[i.kind].append(i)
        return dict(out)


def lint_structure(knowledge_root: Path | str | None = None) -> LintReport:
    root = Path(knowledge_root or os.getenv("RC_KNOWLEDGE_DIR", "knowledge"))
    wiki = root / "wiki"
    rep = LintReport()
    if not wiki.exists():
        return rep

    pages: dict[str, object] = {}   # "type/slug.md" -> WikiPage
    keys: set[str] = set()          # "type:slug"
    for p in sorted(wiki.rglob("*.md")):
        if p.name in _SKIP:
            continue
        page = parse_page(p.read_text(encoding="utf-8"), slug=p.stem)
        rel = f"{page.type}/{p.name}"
        pages[rel] = page
        keys.add(f"{page.type}:{page.slug}")
    rep.pages = len(pages)

    inbound: dict[str, int] = defaultdict(int)
    for rel, page in pages.items():
        if not page.body.strip():
            rep.issues.append(LintIssue(kind="empty", page=rel))
        for link in page.related:
            m = _LINK.search(link)
            if not m:
                rep.issues.append(LintIssue(kind="broken_link", page=rel, detail=f"malformed {link!r}"))
                continue
            key = f"{m.group(1)}:{m.group(2)}"
            if key in keys:
                inbound[key] += 1
            else:
                rep.issues.append(LintIssue(kind="broken_link", page=rel, detail=link))

    # orphans: a non-anchor page nothing links to (papers/ anchors are roots, exempt)
    for rel, page in pages.items():
        if page.type == "papers":
            continue
        if inbound.get(f"{page.type}:{page.slug}", 0) == 0:
            rep.issues.append(LintIssue(kind="orphan", page=rel))

    # index drift: index entries vs files on disk
    index = wiki / "index.md"
    listed = set(_INDEX_LINK.findall(index.read_text(encoding="utf-8"))) if index.exists() else set()
    on_disk = set(pages)
    for rel in sorted(listed - on_disk):
        rep.issues.append(LintIssue(kind="index_drift", page="index.md", detail=f"links missing file {rel}"))
    for rel in sorted(on_disk - listed):
        rep.issues.append(LintIssue(kind="index_drift", page="index.md", detail=f"missing entry for {rel}"))
    return rep


def append_lint_log(knowledge_root: Path | str | None, report: LintReport, when: str) -> None:
    root = Path(knowledge_root or os.getenv("RC_KNOWLEDGE_DIR", "knowledge"))
    path = root / "wiki" / "log.md"
    counts = {k: len(v) for k, v in report.by_kind().items()}
    entry = f"\n## [{when}] lint\n- pages: {report.pages} · issues: {len(report.issues)} {counts or ''}\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Wiki log\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


# ---- optional semantic audit (opt-in; needs the librarian model) ----------

class AuditResult(BaseModel):
    contradictions: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


AUDIT_SYS = (
    "You audit an AI4SE knowledge wiki. Given findings/gaps pages, list only REAL issues: "
    "(a) contradictions — claims that conflict across pages (name the pages); (b) gaps — open "
    "problems implied but not captured. Be concise; if none, return empty lists."
)


async def lint_semantic(model, knowledge_root: Path | str | None = None, *,
                        types=("findings", "gaps", "concepts"), max_chars: int = 12000) -> AuditResult:
    """One bounded LLM pass over the synthesis pages. `model` = a PydanticAI model/string."""
    from pydantic_ai import Agent

    root = Path(knowledge_root or os.getenv("RC_KNOWLEDGE_DIR", "knowledge"))
    chunks: list[str] = []
    for t in types:
        for p in sorted((root / "wiki" / t).rglob("*.md")) if (root / "wiki" / t).exists() else []:
            chunks.append(f"### {t}/{p.stem}\n{p.read_text(encoding='utf-8')[:1500]}")
    corpus = "\n\n".join(chunks)[:max_chars]
    if not corpus.strip():
        return AuditResult()
    agent: Agent = Agent(model, output_type=AuditResult, system_prompt=AUDIT_SYS)
    return (await agent.run(f"Wiki pages:\n{corpus}")).output
