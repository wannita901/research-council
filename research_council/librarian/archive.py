"""Library archive / reset / restore (human-triggered only; plan/16 §3).

The "library" = the LLM-wiki plus its immutable external sources. Reset always archives
first (unless --hard); restore archives the current state before overwriting, so nothing
is ever lost without a snapshot. Archives live under knowledge/.archive/<stamp>/ (gitignored).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ARCHIVE = ".archive"
SUBDIRS = ["wiki", "raw/external"]  # what constitutes the library

_INDEX = """# Wiki index

Catalog of all wiki pages (one line each, by category). Regenerated on every ingest.

_(empty — seed with `council ingest <source>`)_
"""
_LOG = "# Wiki log\n\nAppend-only history of ingests and lint passes.\n"


def _root(knowledge_root: Path | str | None) -> Path:
    return Path(knowledge_root or os.getenv("RC_KNOWLEDGE_DIR", "knowledge"))


def list_archives(knowledge_root: Path | str | None = None) -> list[str]:
    d = _root(knowledge_root) / ARCHIVE
    return sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True) if d.exists() else []


def archive_library(knowledge_root: Path | str | None, stamp: str) -> str:
    root = _root(knowledge_root)
    dest = root / ARCHIVE / stamp
    for sub in SUBDIRS:
        src = root / sub
        if src.exists():
            shutil.copytree(src, dest / sub, dirs_exist_ok=True)
    return stamp


def _init_empty(root: Path) -> None:
    wiki = root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text(_INDEX, encoding="utf-8")
    (wiki / "log.md").write_text(_LOG, encoding="utf-8")
    (root / "raw" / "external").mkdir(parents=True, exist_ok=True)


def _clear(root: Path) -> None:
    for sub in SUBDIRS:
        p = root / sub
        if p.exists():
            shutil.rmtree(p)


def reset_library(knowledge_root: Path | str | None, stamp: str, *, hard: bool = False) -> str | None:
    """Clear the library back to an empty wiki. Archives first unless `hard`. Returns the
    archive stamp (or None for a hard wipe)."""
    root = _root(knowledge_root)
    archived = None if hard else archive_library(root, stamp)
    _clear(root)
    _init_empty(root)
    return archived


def restore_library(knowledge_root: Path | str | None, stamp: str, *,
                    backup_stamp: str | None = None) -> None:
    """Overwrite the current library with archive <stamp>. Backs up current first if asked."""
    root = _root(knowledge_root)
    src = root / ARCHIVE / stamp
    if not src.exists():
        raise FileNotFoundError(f"no archive {stamp!r}")
    if backup_stamp:
        archive_library(root, backup_stamp)
    _clear(root)
    for sub in SUBDIRS:
        s = src / sub
        if s.exists():
            shutil.copytree(s, root / sub, dirs_exist_ok=True)
    if not (root / "wiki" / "index.md").exists():
        _init_empty(root)
