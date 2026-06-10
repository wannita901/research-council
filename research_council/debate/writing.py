"""Stage C engine (plan/18) — council writing loop: draft → PC review → revise → accept.

One lead Writer drafts the whole paper (markdown). Two PC reviewers score it against the
venue rubric and file section-tagged change-requests. The lead revises ONLY the touched
sections; we re-review. The loop accepts when the mean rubric score clears the bar with no
blocking change-request — else it revises until the revision cap / USD budget binds and keeps
the best-scoring draft. A final coherence pass smooths section-level edits, then the accepted
paper is exported to LaTeX (build-verify-fix) if a TeX engine is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from research_council.config import CONFIG_DIR
from research_council.debate.caps import StageCCaps, stage_c_caps, total_spend
from research_council.store.models import (
    Citation,
    PaperDraft,
    ReviewNotes,
    WritingResult,
)

_ORDER = ["Introduction", "Related Work", "Method", "Experiment", "Results", "Conclusion"]
Emit = Callable[[str, str, dict], None] | None


# --- venue + file helpers -----------------------------------------------------
def load_venue(name: str) -> dict:
    path = CONFIG_DIR / "venues" / f"{name}.yaml"
    if not path.exists():
        path = CONFIG_DIR / "venues" / "generic.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def list_venues() -> list[str]:
    return sorted(p.stem for p in (CONFIG_DIR / "venues").glob("*.yaml"))


async def grounded_citations(idea: dict, *, k: int = 8) -> list[Citation]:
    """Build the allowed-citation list from LLM-wiki prior-art pages (origin:external only).

    These carry real provenance, so the writer can cite them without inventing keys. Returns
    [] when the wiki is empty (the writer is then instructed to cite nothing)."""
    import re

    from research_council.retrieval.wiki import WikiProvider

    q = " ".join(str(idea.get(x, "")) for x in ("title", "hypothesis", "method")).strip()
    if not q:
        return []
    papers = await WikiProvider().search(q, k=k, external_only=True)
    out, seen = [], set()
    for p in papers:
        base = re.sub(r"[^a-z0-9]+", "", (p.title or "ref").lower())[:18] or "ref"
        key = base
        n = 2
        while key in seen:
            key = f"{base}{n}"
            n += 1
        seen.add(key)
        out.append(Citation(key=key, text=p.title, source_id=p.id, grounded=True))
    return out


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "section"


def _ordered(sections: dict[str, str]) -> list[tuple[str, str]]:
    keys = sorted(sections, key=lambda k: (_ORDER.index(k) if k in _ORDER else len(_ORDER), k))
    return [(k, sections[k]) for k in keys]


# --- review aggregation -------------------------------------------------------
def _merge_reviews(reviews: list[ReviewNotes], block_severities: tuple[str, ...]):
    """Return (merged ReviewNotes, aggregate mean, is_blocking)."""
    crits: dict[str, list[float]] = {}
    comments, changes = [], []
    for rv in reviews:
        for k, v in rv.scores.items():
            crits.setdefault(k, []).append(v)
        comments += [f"[{rv.reviewer_vendor}] {c}" for c in rv.comments]
        changes += rv.change_requests
    scores = {k: round(sum(v) / len(v), 4) for k, v in crits.items()}
    merged = ReviewNotes(scores=scores, comments=comments, change_requests=changes,
                         verdict=reviews[0].verdict if reviews else "")
    blocking = any(c.severity in block_severities for c in changes)
    return merged, merged.mean, blocking


def _sections_to_revise(changes) -> list[str]:
    out = []
    for c in changes:
        name = c.section.strip()
        if name and name not in ("Abstract", "Title") and name not in out:
            out.append(name)
    return out


def _merge_revision(base: PaperDraft, revised: PaperDraft, sections: list[str], changes) -> PaperDraft:
    """Keep `base`, overwriting only the targeted sections (+ abstract/title if requested)."""
    merged = base.model_copy(deep=True)
    for name in sections:
        if name in revised.sections:
            merged.sections[name] = revised.sections[name]
    targeted = {c.section for c in changes}
    if "Abstract" in targeted and revised.abstract:
        merged.abstract = revised.abstract
    if "Title" in targeted and revised.title:
        merged.title = revised.title
    return merged


# --- paper files --------------------------------------------------------------
def _write_paper(out_dir: Path, draft: PaperDraft, review: ReviewNotes, venue_name: str,
                 result: WritingResult) -> Path:
    paper = out_dir / "paper"
    (paper / "sections").mkdir(parents=True, exist_ok=True)
    body = [f"# {draft.title}", f"*Target venue: {venue_name} · draft by research-council*",
            "", "## Abstract", draft.abstract, ""]
    for name, text in _ordered(draft.sections):
        body += [f"## {name}", text, ""]
        (paper / "sections" / f"{_slug(name)}.md").write_text(f"# {name}\n\n{text}\n", encoding="utf-8")
    if draft.figure:
        body += ["## Figure", f"![results]({draft.figure})", ""]
    if draft.citations:
        body += ["## References"]
        for c in draft.citations:
            tag = "" if c.grounded else "  _(needs verification)_"
            body.append(f"- [{c.key}] {c.text}{tag}")
        body.append("")
    (paper / "paper.md").write_text("\n".join(body).strip() + "\n", encoding="utf-8")

    rv = [f"# Review — {venue_name}", "",
          f"**Status:** {'accepted' if result.accepted else 'best-so-far (' + result.stopped_reason + ')'}",
          f"**Mean rubric score:** {review.mean:.2f}", "", "## Scores"]
    rv += [f"- **{k}**: {v:.2f}" for k, v in review.scores.items()]
    rv += ["", "## Comments", *[f"- {c}" for c in review.comments]]
    if review.change_requests:
        rv += ["", "## Outstanding change-requests"]
        rv += [f"- [{c.severity}] ({c.section or 'whole'}) {c.msg}" for c in review.change_requests]
    rv += ["", f"**Score history:** {', '.join(f'{s:.2f}' for s in result.score_history)}",
           f"**Verdict:** {review.verdict}"]
    (paper / "review.md").write_text("\n".join(rv) + "\n", encoding="utf-8")
    return paper / "paper.md"


# --- the loop -----------------------------------------------------------------
async def run_writing(handoff, writer, reviewers, *, venue: str, out_dir: Path | str,
                      caps: StageCCaps | None = None, profile: str = "balanced",
                      allowed_citations: list[Citation] | None = None,
                      latex: bool = True, emit: Emit = None) -> WritingResult:
    caps = caps or stage_c_caps(profile)
    venue_cfg = load_venue(venue)
    venue_name = venue_cfg.get("name", venue)
    rubric = venue_cfg.get("rubric", {})
    experiment = handoff.artifacts or {}
    out_dir = Path(out_dir)

    # results figure from the real metric (best-effort, trusted host code — not LLM output)
    figure = ""
    if experiment.get("metric") or experiment.get("rqs"):
        from research_council.verify.figure import render_result_figure
        figure = render_result_figure(experiment, out_dir / "paper" / "assets") or ""
        if figure:
            figure = str(Path(figure).relative_to(out_dir / "paper")) if str(figure).startswith(
                str(out_dir / "paper")) else figure

    draft = await writer.draft(handoff.idea, experiment, handoff.constraints,
                               allowed_citations=allowed_citations, figure=figure)
    if emit:
        emit("writing", "draft", {"title": draft.title, "sections": list(draft.sections),
                                  "citations": len(draft.citations)})

    score_history: list[float] = []
    best = None  # (mean, draft, merged_review)
    accepted = False
    reason = "revisions_exhausted"
    merged = ReviewNotes()

    for rnd in range(1, caps.max_revisions + 1):
        reviews = [await rv.review(draft, rubric) for rv in reviewers]
        merged, mean, blocking = _merge_reviews(reviews, caps.block_severities)
        score_history.append(mean)
        if emit:
            emit("writing", "review", {"round": rnd, "mean": mean, "blocking": blocking,
                                       "verdict": merged.verdict,
                                       "change_requests": len(merged.change_requests)})
        if best is None or mean > best[0]:
            best = (mean, draft.model_copy(deep=True), merged)

        if mean >= caps.accept and not blocking:
            accepted, reason = True, "accepted"
            break

        spent = total_spend(writer, *reviewers)
        if caps.usd_budget and spent >= caps.usd_budget:
            reason = "budget_exhausted"
            break
        if rnd == caps.max_revisions:
            break  # don't revise after the last review

        sections = _sections_to_revise(merged.change_requests)
        if not sections and not any(c.section in ("Abstract", "Title") for c in merged.change_requests):
            sections = ["Results", "Method"]  # nothing tagged → revise the empirical core
        revised = await writer.revise(draft, merged.change_requests, sections)
        draft = _merge_revision(draft, revised, sections, merged.change_requests)
        if emit:
            emit("writing", "revise", {"round": rnd, "sections": sections})

    # use best-scoring draft if we never cleared the bar
    final_mean, final_draft, final_review = best if best else (0.0, draft, merged)
    if not accepted:
        draft = final_draft
        merged = final_review

    # final coherence pass to smooth section-level edits
    draft = await writer.coherence_pass(draft)
    if emit:
        emit("writing", "coherence_pass", {"sections": list(draft.sections)})

    result = WritingResult(
        venue=venue, title=draft.title, sections=list(draft.sections), review=merged,
        score_history=score_history, revisions=len(score_history), accepted=accepted,
        citations=draft.citations, usd=total_spend(writer, *reviewers), stopped_reason=reason)
    paper_md = _write_paper(out_dir, draft, merged, venue_name, result)
    result.paper_path = str(paper_md)

    if latex:
        from research_council.verify.latex import build_paper_latex
        lx = build_paper_latex(draft, out_dir / "paper", venue_cfg, attempts=caps.latex_fix_attempts,
                               emit=emit)
        result.latex = lx.get("status", "skipped")
        result.pdf_path = lx.get("pdf", "")
        if emit:
            emit("writing", "latex", {"status": result.latex, "pdf": bool(result.pdf_path)})
    else:
        result.latex = "skipped"
    return result
