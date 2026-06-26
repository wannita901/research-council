"""Stage C engine (plan/18) — council writing loop: draft → PC review → revise → accept.

One lead Writer drafts the whole paper (markdown). Two PC reviewers score it against the
venue rubric and file section-tagged change-requests. The lead revises ONLY the touched
sections; we re-review. The loop accepts when the mean rubric score clears the bar with no
blocking change-request — else it revises until the revision cap / USD budget binds and keeps
the best-scoring draft. A final coherence pass smooths section-level edits, then the accepted
paper is exported to LaTeX (build-verify-fix) if a TeX engine is available.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from research_council.config import CONFIG_DIR
from research_council.debate.caps import StageCCaps, stage_c_caps, total_spend
from research_council.store.models import (
    Citation,
    PaperDraft,
    ReviewNotes,
    WritingResult,
)
from research_council.verify.figure import caption_for_figure

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
    merged = ReviewNotes(
        scores=scores,
        comments=comments,
        change_requests=changes,
        verdict=reviews[0].verdict if reviews else "",
    )
    blocking = any(c.severity in block_severities for c in changes)
    return merged, merged.mean, blocking


def _sections_to_revise(changes) -> list[str]:
    out = []
    for c in changes:
        name = c.section.strip()
        if name and name not in ("Abstract", "Title") and name not in out:
            out.append(name)
    return out


def _merge_revision(
    base: PaperDraft, revised: PaperDraft, sections: list[str], changes
) -> PaperDraft:
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
def _write_paper(
    out_dir: Path, draft: PaperDraft, review: ReviewNotes, venue_name: str, result: WritingResult
) -> Path:
    paper = out_dir / "paper"
    (paper / "sections").mkdir(parents=True, exist_ok=True)
    body = [
        f"# {draft.title}",
        f"*Target venue: {venue_name} · draft by research-council*",
        "",
        "## Abstract",
        draft.abstract,
        "",
    ]
    for name, text in _ordered(draft.sections):
        body += [f"## {name}", text, ""]
        (paper / "sections" / f"{_slug(name)}.md").write_text(
            f"# {name}\n\n{text}\n", encoding="utf-8"
        )
    if draft.figures:
        body += ["## Figures"]
        for i, fig in enumerate(draft.figures, 1):
            cap = caption_for_figure(fig)
            body += [f"**Figure {i}.** {cap}", f"![figure {i}]({fig})", ""]
    if draft.citations:
        body += ["## References"]
        for c in draft.citations:
            tag = "" if c.grounded else "  _(needs verification)_"
            body.append(f"- [{c.key}] {c.text}{tag}")
        body.append("")
    (paper / "paper.md").write_text("\n".join(body).strip() + "\n", encoding="utf-8")

    rv = [
        f"# Review — {venue_name}",
        "",
        f"**Status:** {'accepted' if result.accepted else 'best-so-far (' + result.stopped_reason + ')'}",
        f"**Mean rubric score:** {review.mean:.2f}",
        "",
        "## Scores",
    ]
    rv += [f"- **{k}**: {v:.2f}" for k, v in review.scores.items()]
    rv += ["", "## Comments", *[f"- {c}" for c in review.comments]]
    if review.change_requests:
        rv += ["", "## Outstanding change-requests"]
        rv += [f"- [{c.severity}] ({c.section or 'whole'}) {c.msg}" for c in review.change_requests]
    rv += [
        "",
        f"**Score history:** {', '.join(f'{s:.2f}' for s in result.score_history)}",
        f"**Verdict:** {review.verdict}",
    ]
    (paper / "review.md").write_text("\n".join(rv) + "\n", encoding="utf-8")
    return paper / "paper.md"


def _collect_experiment_figures(out_dir: Path) -> list[str]:
    """Copy the real figures Stage B saved (experiment/<rq>/figures/*) into paper/assets/,
    prefixed by RQ to avoid collisions. Returns their paths relative to the paper dir.

    Figures from a NON-feasible RQ are skipped: a non-feasible run errored or never emitted a
    valid METRIC, so any plot it left on disk is from a broken experiment and must not enter the
    paper as evidence (the prose honesty-gate frames unapproved *text*, but a chart is a stronger
    visual claim). Filtering is conservative — a figure is dropped only when results.csv positively
    marks its RQ feasible=False; with no results.csv (no signal) or an unlisted RQ, the figure is
    kept so pre-producer projects are unaffected.

    A figure that is not a structurally-valid, non-empty image (0-byte/truncated/garbage left by a
    half-failed savefig, or a non-image file) is also skipped: a broken image is not correct
    evidence and would break the LaTeX \\includegraphics it ends up in."""
    from research_council.verify.approval import feasibility_by_rq
    from research_council.verify.figure import is_valid_figure

    exp = Path(out_dir) / "experiment"
    assets = Path(out_dir) / "paper" / "assets"
    rels: list[str] = []
    if not exp.is_dir():
        return rels
    feasible = feasibility_by_rq(out_dir)
    for sub in sorted(p for p in exp.iterdir() if p.is_dir()):
        if feasible.get(sub.name) is False:
            continue  # broken/non-feasible experiment → its figure is not valid evidence
        figdir = sub / "figures"
        if not figdir.is_dir():
            continue
        for p in sorted(figdir.glob("*")):
            if p.is_file() and p.suffix.lower() in (".png", ".pdf", ".svg"):
                if not is_valid_figure(p):
                    continue  # empty/corrupt/non-image plot → not valid evidence
                assets.mkdir(parents=True, exist_ok=True)
                dest = f"{sub.name}_{p.name}"
                (assets / dest).write_bytes(p.read_bytes())
                rels.append(f"assets/{dest}")
    return rels


def load_prior_paper(out_dir: Path | str):
    """Reload a previous Stage-C draft so a re-run IMPROVES it instead of redrafting.
    Returns (PaperDraft | None, build_error). Reads paper/sections/*.md + paper.md; the
    build_error is the saved build.log when the last compile produced no PDF."""
    paper = Path(out_dir) / "paper"
    pmd = paper / "paper.md"
    if not pmd.exists():
        return None, ""
    text = pmd.read_text(encoding="utf-8")
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "")

    def _section(body: str, name: str) -> str:
        out, grabbing = [], False
        for ln in body.splitlines():
            if ln.startswith("## "):
                grabbing = ln[3:].strip() == name
                continue
            if grabbing:
                out.append(ln)
        return "\n".join(out).strip()

    abstract = _section(text, "Abstract")
    sections: dict[str, str] = {}
    sd = paper / "sections"
    if sd.is_dir():
        for f in sorted(sd.glob("*.md")):
            t = f.read_text(encoding="utf-8")
            lines = t.splitlines()
            name = lines[0][1:].strip() if lines and lines[0].startswith("#") else f.stem
            sections[name] = "\n".join(lines[1:]).strip()
    draft = PaperDraft(title=title, abstract=abstract, sections=sections)

    build_error = ""
    log = paper / "build.log"
    if log.exists() and not (paper / "paper.pdf").exists():
        build_error = log.read_text(encoding="utf-8")[-1500:]
    return draft, build_error


# --- the loop -----------------------------------------------------------------
async def run_writing(
    handoff,
    writer,
    reviewers,
    *,
    venue: str,
    out_dir: Path | str,
    caps: StageCCaps | None = None,
    profile: str = "balanced",
    allowed_citations: list[Citation] | None = None,
    prior_draft: PaperDraft | None = None,
    build_error: str = "",
    latex_fixer=None,
    latex: bool = True,
    bib_providers=None,
    emit: Emit = None,
) -> WritingResult:
    caps = caps or stage_c_caps(profile)
    venue_cfg = load_venue(venue)
    venue_name = venue_cfg.get("name", venue)
    rubric = venue_cfg.get("rubric", {})
    experiment = handoff.artifacts or {}
    out_dir = Path(out_dir)

    # plan/25 Gap 4: read back the council's own approval signal (results.csv `approved` column).
    # If not every RQ was approved, inject an honesty constraint so the draft frames unapproved
    # work as a feasibility/negative result instead of overclaiming. This must happen BEFORE the
    # draft so the very first version is framed honestly.
    from research_council.verify.approval import (
        approval_status,
        approval_to_change_request,
        honesty_constraint,
    )

    approval = approval_status(out_dir)
    _honesty = honesty_constraint(approval)
    if _honesty:
        handoff.constraints = {**(handoff.constraints or {}), "approval_honesty": _honesty}

    # Prefer the REAL figures the experiment saved (Stage B); copy them into paper/assets/.
    # Fall back to a single host-synthesized chart only if the experiment produced none.
    figures = _collect_experiment_figures(out_dir)
    if not figures and (experiment.get("metric") or experiment.get("rqs")):
        from research_council.verify.figure import render_result_figure

        host = render_result_figure(experiment, out_dir / "paper" / "assets")
        if host:
            figures = [f"assets/{Path(host).name}"]

    if prior_draft is not None:
        # continue improving the existing paper instead of redrafting from scratch
        draft = prior_draft
        if figures and not draft.figures:
            draft.figures = figures
        if emit:
            emit(
                "writing",
                "continue",
                {"sections": list(draft.sections), "build_error": bool(build_error)},
            )
    else:
        draft = await writer.draft(
            handoff.idea,
            experiment,
            handoff.constraints,
            allowed_citations=allowed_citations,
            figures=figures,
        )
        if emit:
            emit(
                "writing",
                "draft",
                {
                    "title": draft.title,
                    "sections": list(draft.sections),
                    "citations": len(draft.citations),
                },
            )

    # plan/25 Gap 1: the evidence the paper's numbers must match (experiment/results.csv).
    # Loaded once; each review round folds any UNBACKED numeric claim into the change-requests
    # so the writer must cite/back/remove it — turning the post-hoc flag into a feedback loop.
    from research_council.verify.claims import check_draft, claims_to_change_requests, load_evidence

    evidence = load_evidence(out_dir)

    score_history: list[float] = []
    best = None  # (mean, draft, merged_review)
    accepted = False
    reason = "revisions_exhausted"
    merged = ReviewNotes()

    for rnd in range(1, caps.max_revisions + 1):
        reviews = [await rv.review(draft, rubric) for rv in reviewers]
        merged, mean, blocking = _merge_reviews(reviews, caps.block_severities)

        # Fold unbacked numeric claims into THIS round's change-requests. They drive the
        # writer's revision (sections_to_revise) and, when caps.claims_unbacked_block is on,
        # block acceptance until resolved or the revision cap binds.
        claim_report = check_draft(draft, evidence)
        claim_crs = claims_to_change_requests(claim_report)
        if claim_crs:
            merged.change_requests.extend(claim_crs)
            # Only BLOCK on unbacked claims when there is actually evidence to back them
            # against. With no results.csv every numeric claim reads as unbacked, so blocking
            # here would make any paper with a number unacceptable forever — the same
            # "no results → no signal, don't gate" stance approval_to_change_request takes.
            # The change-requests still surface as feedback either way.
            if caps.claims_unbacked_block and evidence:
                blocking = True

        # plan/25 Gap 4: when the council approved ZERO RQs and unapproved_block is on, the paper
        # cannot ship as accepted — fold in a high-severity demand and force blocking so it falls
        # back to best-so-far with the honest framing rather than an "accepted" overclaim.
        if caps.unapproved_block:
            approval_cr = approval_to_change_request(approval)
            if approval_cr is not None:
                merged.change_requests.append(approval_cr)
                blocking = True

        score_history.append(mean)
        if emit:
            for rv in reviews:  # per-reviewer detail: who scored what + what they demand
                emit(
                    "writing",
                    "reviewer",
                    {
                        "round": rnd,
                        "vendor": rv.reviewer_vendor,
                        "mean": rv.mean,
                        "verdict": rv.verdict,
                        "change_requests": [
                            {"section": c.section, "severity": c.severity, "msg": c.msg}
                            for c in rv.change_requests
                        ],
                    },
                )
            emit(
                "writing",
                "review",
                {
                    "round": rnd,
                    "mean": mean,
                    "blocking": blocking,
                    "verdict": merged.verdict,
                    "change_requests": len(merged.change_requests),
                },
            )
            if claim_crs:
                emit(
                    "writing",
                    "claims_round",
                    {
                        "round": rnd,
                        "unbacked": len(claim_crs),
                        "blocking": caps.claims_unbacked_block,
                    },
                )
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
        if not sections and not any(
            c.section in ("Abstract", "Title") for c in merged.change_requests
        ):
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
        venue=venue,
        title=draft.title,
        sections=list(draft.sections),
        review=merged,
        score_history=score_history,
        revisions=len(score_history),
        accepted=accepted,
        citations=draft.citations,
        usd=total_spend(writer, *reviewers),
        stopped_reason=reason,
        approved_rqs=approval.approved,
        total_rqs=approval.total,
    )
    if approval.has_results and emit:
        emit(
            "writing",
            "approval",
            {
                "approved": approval.approved,
                "total": approval.total,
                "blocking": caps.unapproved_block and not approval.any_approved,
            },
        )
    paper_md = _write_paper(out_dir, draft, merged, venue_name, result)
    result.paper_path = str(paper_md)

    # Claims-to-evidence check (plan/25 Gap 1): diff the prose's numeric claims against
    # experiment/results.csv and emit paper/claims.json. Surfaces fabricated figures that the
    # writer was *told* not to invent but nothing previously checked.
    from research_council.verify.claims import write_claims_report

    claims = write_claims_report(out_dir)
    if claims is not None:
        result.claims_total = claims.n_claims
        result.claims_unbacked = claims.n_unbacked
        if emit:
            emit(
                "writing",
                "claims",
                {"total": claims.n_claims, "unbacked": claims.n_unbacked},
            )

    # Citation-to-record resolution (plan/25 Gap 2): resolve each citation against the real
    # bibliographic providers and emit paper/references.bib + references.json. Always emits the
    # .bib (UNVERIFIED-tagged when offline / unresolved) so the artifact is a reliable signal.
    from research_council.verify.bib import write_bib

    bib = await write_bib(out_dir, draft, bib_providers)
    result.refs_total = bib.n_total
    result.refs_resolved = bib.n_resolved
    if emit:
        emit("writing", "references", {"total": bib.n_total, "resolved": bib.n_resolved})

    if latex:
        from research_council.verify.latex import build_paper_latex, compile_existing

        paper_dir = out_dir / "paper"
        lx = build_paper_latex(
            draft,
            paper_dir,
            venue_cfg,
            attempts=caps.latex_fix_attempts,
            emit=emit,
            resolutions=bib.resolutions,
        )
        # build-verify-FIX: if the mechanical pass can't compile it, hand the .tex + error log to
        # the council's LaTeX fixer and recompile (bounded) — the fail log IS the feedback.
        if lx.get("status") == "build_failed" and latex_fixer is not None:
            for i in range(1, caps.latex_fix_attempts + 1):
                tex = (paper_dir / "paper.tex").read_text(encoding="utf-8")
                fixed = await latex_fixer.fix(tex, lx.get("log", ""))
                (paper_dir / "paper.tex").write_text(fixed, encoding="utf-8")
                lx = compile_existing(paper_dir)
                if emit:
                    emit("writing", "latex_fix", {"attempt": i, "status": lx.get("status")})
                if lx.get("status") == "built":
                    break
        result.latex = lx.get("status", "skipped")
        result.pdf_path = lx.get("pdf", "")
        if emit:
            emit("writing", "latex", {"status": result.latex, "pdf": bool(result.pdf_path)})
    else:
        result.latex = "skipped"
    return result
