"""Wiki reader — the curated synthesis layer's READ side (plan/8, plan/16).

Network-free: reads markdown under knowledge/wiki/ and ranks pages by keyword
overlap. Each page carries `origin: external|internal` (plan/16 §4) — the contamination
guard. Gap-finding reads everything; a strict prior-art/novelty path passes
`external_only=True` to drop the council's own synthesis. Returns [] until the wiki
is seeded (write side = council ingest).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from research_council.store.models import Paper

_SKIP = {"index.md", "log.md"}
_ORIGIN = re.compile(r"^origin:\s*(\w+)", re.MULTILINE)


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or fallback
    return fallback


def _origin(text: str) -> str:
    m = _ORIGIN.search(text)
    return m.group(1) if m else "external"


class WikiProvider:
    name = "wiki"

    def __init__(self, root: Path | str | None = None):
        base = Path(root or os.getenv("RC_KNOWLEDGE_DIR", "knowledge"))
        self.root = base / "wiki"

    async def search(self, query: str, k: int = 10, *, external_only: bool = False) -> list[Paper]:
        if not self.root.exists():
            return []
        terms = [t.lower() for t in re.findall(r"\w+", query)]
        if not terms:
            return []
        scored: list[tuple[int, Path, str]] = []
        for p in self.root.rglob("*.md"):
            if p.name in _SKIP:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if external_only and _origin(text) == "internal":
                continue  # strict prior-art view: exclude the council's own synthesis
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                scored.append((score, p, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[Paper] = []
        for _, p, text in scored[:k]:
            out.append(
                Paper(
                    id=f"wiki:{p.relative_to(self.root)}",
                    title=_first_heading(text, p.stem),
                    abstract=text.strip()[:300],
                    source="wiki",
                    origin=_origin(text),
                )
            )
        return out
