"""Ingest pipeline (plan/16 §5) — persist the librarian's routed pages into the wiki.

Flow: route a Source → ensure the papers/ anchor links to its fan-out → write/merge the
typed pages → regenerate index.md → append log.md. Auto-merge into wiki/ with atomic
per-file writes; NO git commit (you review with `git diff` and commit on your cadence).

External sources also get an immutable copy saved under raw/external/ (Karpathy: raw is
the source of truth). Internal (council synthesis) writes only wiki/ pages — never raw/.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from research_council.librarian.schema import (
    TAXONOMY,
    Source,
    WikiPage,
    parse_page,
    render_page,
    wiki_path,
)

_SKIP_INDEX = {"index.md", "log.md"}


class IngestReport(BaseModel):
    citekey: str
    written: list[str] = Field(default_factory=list)  # new page rel_paths
    merged: list[str] = Field(default_factory=list)  # existing pages updated
    raw_saved: str | None = None  # raw/external/... if external


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "source"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _summary(body: str) -> str:
    for line in body.splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not s.startswith("<!--"):
            return s[:90]
    return ""


def _link_anchor(pages: list[WikiPage]) -> None:
    """Make the papers/ anchor cross-link to every typed page produced this ingest."""
    anchors = [p for p in pages if p.type == "papers"]
    typed = [p for p in pages if p.type != "papers"]
    for a in anchors:
        a.related = _dedup(a.related + [f"[[{p.type}:{p.slug}]]" for p in typed])


def _merge(existing: WikiPage, new: WikiPage) -> WikiPage:
    """Compound a page: union provenance/links, append the new source's contribution once.
    A page touched by ANY external source counts as external (conservative novelty guard)."""
    marker = f"<!-- src:{new.papers[0] if new.papers else '?'} -->"
    body = existing.body
    if marker not in existing.body and new.body.strip():
        src = new.papers[0] if new.papers else "source"
        body = existing.body.rstrip() + f"\n\n## From {src}\n{marker}\n{new.body.strip()}\n"
    origin = "external" if "external" in (existing.origin, new.origin) else "internal"
    return WikiPage(
        type=existing.type,
        title=existing.title,
        slug=existing.slug,
        origin=origin,
        papers=_dedup(existing.papers + new.papers),
        related=_dedup(existing.related + new.related),
        updated=new.updated or existing.updated,
        body=body,
    )


class Ingestor:
    def __init__(self, librarian, knowledge_root: Path | str | None = None):
        self.librarian = (
            librarian  # any object with async route(Source, updated=) -> list[WikiPage]
        )
        self.root = Path(knowledge_root or os.getenv("RC_KNOWLEDGE_DIR", "knowledge"))

    async def ingest(self, source: Source, *, updated: str | None = None) -> IngestReport:
        updated = updated or datetime.date.today().isoformat()
        report = IngestReport(citekey=source.citekey)

        if source.origin == "external":
            report.raw_saved = self._save_raw(source)

        pages = await self.librarian.route(source, updated=updated)
        if not pages:
            # the router produced nothing — never silently drop a source. Capture it as one page:
            # an external source → its papers/ anchor; the council's synthesis → a findings note.
            pages = [
                WikiPage(
                    type="papers" if source.origin == "external" else "findings",
                    title=source.title or source.citekey,
                    origin=source.origin,
                    papers=[source.citekey],
                    updated=updated,
                    body=(source.text or "").strip()[:1500] or "(no content extracted)",
                )
            ]
        _link_anchor(pages)

        for page in pages:
            path = wiki_path(self.root, page)
            if path.exists():
                merged = _merge(parse_page(path.read_text(encoding="utf-8"), slug=page.slug), page)
                _atomic_write(path, render_page(merged))
                report.merged.append(page.rel_path)
            else:
                _atomic_write(path, render_page(page))
                report.written.append(page.rel_path)

        self._rebuild_index()
        self._append_log(source, report, updated)
        return report

    def _save_raw(self, source: Source) -> str:
        rel = f"raw/external/{_safe(source.citekey)}.md"
        path = self.root / rel
        if not path.exists():
            head = f"# {source.title}\n\n_citekey: {source.citekey}_"
            head += f"  ·  _source: {source.url}_\n\n" if source.url else "\n\n"
            _atomic_write(path, head + source.text.strip() + "\n")
        return rel

    def _rebuild_index(self) -> None:
        """Regenerate index.md from the wiki tree (idempotent, self-healing)."""
        wiki = self.root / "wiki"
        by_type: dict[str, list[tuple[str, str, str]]] = {t: [] for t in TAXONOMY}
        for p in sorted(wiki.rglob("*.md")):
            if p.name in _SKIP_INDEX:
                continue
            page = parse_page(p.read_text(encoding="utf-8"), slug=p.stem)
            if page.type in by_type:
                flag = "" if page.origin == "external" else " _(internal)_"
                by_type[page.type].append(
                    (page.title or p.stem, f"{page.type}/{p.name}", _summary(page.body) + flag)
                )
        lines = [
            "# Wiki index",
            "",
            "Catalog of all wiki pages (one line each, by category). Regenerated on every "
            "ingest; peers consult it to navigate without embeddings at small scale.",
            "",
        ]
        total = 0
        for t in TAXONOMY:
            lines.append(f"## {t}")
            for title, href, summ in by_type[t]:
                total += 1
                lines.append(f"- [{title}]({href})" + (f" — {summ}" if summ else ""))
            lines.append("")
        if total == 0:
            lines.insert(4, "_(empty — seed with `council ingest <source>`)_\n")
        _atomic_write(wiki / "index.md", "\n".join(lines).rstrip() + "\n")

    def _append_log(self, source: Source, report: IngestReport, when: str) -> None:
        path = self.root / "wiki" / "log.md"
        touched = ", ".join(report.written + report.merged) or "(none)"
        entry = (
            f"\n## [{when}] ingest | {source.title} ({source.origin})\n"
            f"- citekey: {source.citekey}\n"
            f"- pages: {touched}\n"
            f"- new: {len(report.written)} · merged: {len(report.merged)}\n"
        )
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Wiki log\n"
        _atomic_write(path, existing.rstrip() + "\n" + entry)
