"""Library archive / reset / restore — pure file ops, offline."""

from __future__ import annotations

import pytest

from research_council.librarian.archive import (
    archive_library,
    list_archives,
    reset_library,
    restore_library,
)


def _seed(root):
    (root / "wiki" / "findings").mkdir(parents=True)
    (root / "wiki" / "findings" / "x.md").write_text("page x", encoding="utf-8")
    (root / "raw" / "external").mkdir(parents=True)
    (root / "raw" / "external" / "s.md").write_text("source", encoding="utf-8")


def test_archive_then_reset_then_restore(tmp_path):
    _seed(tmp_path)
    archive_library(tmp_path, "A1")
    assert (tmp_path / ".archive/A1/wiki/findings/x.md").read_text() == "page x"
    assert "A1" in list_archives(tmp_path)

    archived = reset_library(tmp_path, "A2")
    assert archived == "A2"
    assert not (tmp_path / "wiki/findings/x.md").exists()         # cleared
    assert (tmp_path / "wiki/index.md").exists()                  # reinitialised empty
    assert (tmp_path / ".archive/A2/wiki/findings/x.md").exists()  # archived before clearing

    restore_library(tmp_path, "A1", backup_stamp="B1")
    assert (tmp_path / "wiki/findings/x.md").read_text() == "page x"   # A1 content back
    assert (tmp_path / ".archive/B1/wiki/index.md").exists()           # the empty state was backed up


def test_reset_hard_skips_archive(tmp_path):
    _seed(tmp_path)
    assert reset_library(tmp_path, "X", hard=True) is None
    assert not (tmp_path / ".archive/X").exists()  # nothing archived
    assert (tmp_path / "wiki/index.md").exists() and not (tmp_path / "wiki/findings/x.md").exists()


def test_restore_unknown_archive_raises(tmp_path):
    _seed(tmp_path)
    with pytest.raises(FileNotFoundError):
        restore_library(tmp_path, "nope")


def test_list_archives_newest_first(tmp_path):
    _seed(tmp_path)
    for s in ("20260101T000000", "20260607T120000", "20260301T000000"):
        archive_library(tmp_path, s)
    assert list_archives(tmp_path) == ["20260607T120000", "20260301T000000", "20260101T000000"]
