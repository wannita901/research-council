"""Wiki page schema + frontmatter I/O (plan/16, plan/9; Karpathy LLM-Wiki conventions).

A wiki page is markdown with YAML frontmatter. `origin` is the contamination guard
(plan/16 §4): `external` pages = real prior art; `internal` = the council's own synthesis,
never counted as prior art for novelty. Folders are the 9-type taxonomy.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

# The 9 typed folders (plan/9). approaches=built · methods=validated · concepts=idea · motivations=why.
TAXONOMY = [
    "papers", "tasks", "motivations", "concepts", "approaches",
    "methods", "benchmarks", "findings", "gaps",
]
ORIGINS = ("external", "internal")


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60] or "untitled"


class Source(BaseModel):
    """One thing to ingest. Its text is treated purely as DATA (prompt-injection safe)."""

    citekey: str  # stable id, e.g. "openalex:W123" or "council:run-abc-gap"
    title: str
    text: str = ""  # abstract / notes / council discussion
    origin: str = "external"  # external (real source) | internal (council synthesis)
    url: str | None = None


class WikiPage(BaseModel):
    type: str  # one of TAXONOMY
    title: str
    slug: str = ""  # kebab; filename stem (derived from title if blank)
    origin: str = "external"
    papers: list[str] = Field(default_factory=list)   # source citekeys this page draws on
    related: list[str] = Field(default_factory=list)  # cross-links as "[[type:slug]]"
    updated: str = ""  # YYYY-MM-DD
    body: str = ""

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in TAXONOMY:
            raise ValueError(f"type {v!r} not in taxonomy {TAXONOMY}")
        return v

    @field_validator("origin")
    @classmethod
    def _check_origin(cls, v: str) -> str:
        return v if v in ORIGINS else "external"

    def model_post_init(self, _ctx) -> None:
        if not self.slug:
            self.slug = slugify(self.title)

    @property
    def rel_path(self) -> str:
        return f"{self.type}/{self.slug}.md"


def render_page(p: WikiPage) -> str:
    fm = {"type": p.type, "title": p.title, "origin": p.origin,
          "papers": list(p.papers), "related": list(p.related), "updated": p.updated}
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{p.body.strip()}\n"


def parse_page(text: str, *, slug: str = "") -> WikiPage:
    fm: dict = {}
    body = text
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2]
    return WikiPage(
        type=fm.get("type", "concepts"),
        title=fm.get("title", ""),
        slug=slug or slugify(fm.get("title", "")),
        origin=fm.get("origin", "external"),
        papers=fm.get("papers") or [],
        related=fm.get("related") or [],
        updated=fm.get("updated", ""),
        body=body.strip(),
    )


def wiki_path(knowledge_root: Path | str, page: WikiPage) -> Path:
    """Absolute path a page lives at: <knowledge>/wiki/<type>/<slug>.md."""
    return Path(knowledge_root) / "wiki" / page.rel_path
