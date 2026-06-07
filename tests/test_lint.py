"""Wiki structural lint — pure/offline checks."""

from __future__ import annotations

from research_council.librarian.lint import append_lint_log, lint_structure
from research_council.librarian.schema import WikiPage, render_page


def _write(root, page: WikiPage):
    p = root / "wiki" / page.type / f"{page.slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_page(page), encoding="utf-8")


def test_lint_clean_wiki(tmp_path):
    (tmp_path / "wiki").mkdir(parents=True)
    rep = lint_structure(tmp_path)
    assert rep.pages == 0 and rep.issues == []


def test_lint_flags_broken_links_orphans_empty_and_drift(tmp_path):
    _write(tmp_path, WikiPage(type="papers", title="Anchor",
                              related=["[[approaches:good]]", "[[concepts:missing-page]]"], body="anchor"))
    _write(tmp_path, WikiPage(type="approaches", title="Good", body="content"))   # linked → not orphan
    _write(tmp_path, WikiPage(type="gaps", title="Orphan", body="x"))             # no inbound → orphan
    _write(tmp_path, WikiPage(type="findings", title="Empty", body=""))           # empty body
    (tmp_path / "wiki" / "index.md").write_text("# Wiki index\n", encoding="utf-8")  # lists nothing → drift

    kinds = lint_structure(tmp_path).by_kind()
    assert any("missing-page" in i.detail for i in kinds.get("broken_link", []))
    orphans = {i.page for i in kinds.get("orphan", [])}
    assert "gaps/orphan.md" in orphans and "approaches/good.md" not in orphans  # papers anchor exempt; linked page exempt
    assert any(i.page == "findings/empty.md" for i in kinds.get("empty", []))
    assert kinds.get("index_drift")  # 4 files on disk, none catalogued


def test_lint_log_appended(tmp_path):
    (tmp_path / "wiki").mkdir(parents=True)
    rep = lint_structure(tmp_path)
    append_lint_log(tmp_path, rep, "2026-06-07")
    assert "[2026-06-07] lint" in (tmp_path / "wiki" / "log.md").read_text()
