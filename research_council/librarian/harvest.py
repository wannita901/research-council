"""Post-run harvest (plan/16 §5) — compound the wiki from a finished ideation run.

Two streams, both Karpathy-blessed ("good answers filed back into the wiki"):
  • internal — the council's own synthesis (selected idea + gaps + key critiques) → one
    `origin: internal` source (never counted as prior art).
  • external — the real papers the council cited (brief.refs) whose metadata is in the
    retrieval cache → `origin: external` sources (capped to bound cost).

Pure over parsed trace events + a {paper_id: Paper} lookup; `Ingestor` does the writing.
Offline-testable. Always run post-run / opt-in, never mid-debate.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from research_council.librarian.schema import Source

HARVEST_CAP = 8  # max external papers routed per run (each is one LLM call)


class HarvestReport(BaseModel):
    internal: list[str] = Field(default_factory=list)  # pages written/merged for the synthesis
    external: list[str] = Field(default_factory=list)  # citekeys ingested
    skipped_external: int = 0  # cited papers over the cap / not in cache


def run_meta(events: list[dict]) -> tuple[str, str]:
    run_id = events[0].get("run_id", "run") if events else "run"
    topic = ""
    for e in events:
        if e.get("kind") == "topic":
            topic = e.get("payload", {}).get("topic", "")
            break
    return run_id, topic


def collect_external(events: list[dict], papers_by_id: dict, cap: int = HARVEST_CAP):
    """Cited refs that we have metadata for → external Sources (deduped, capped)."""
    refs: list[str] = []
    seen: set[str] = set()
    for e in events:
        if e.get("kind") == "research_brief":
            for r in e.get("payload", {}).get("refs") or []:
                if r in papers_by_id and r not in seen:
                    seen.add(r)
                    refs.append(r)
    skipped = max(0, len(refs) - cap)
    out: list[Source] = []
    for pid in refs[:cap]:
        p = papers_by_id[pid]
        out.append(
            Source(
                citekey=pid,
                title=getattr(p, "title", pid),
                text=getattr(p, "abstract", "") or getattr(p, "title", ""),
                origin="external",
                url=getattr(p, "url", None),
            )
        )
    return out, skipped


def build_internal(events: list[dict], topic: str, run_id: str) -> Source | None:
    """Consolidate the run's synthesis into one internal source for the librarian to route."""
    gaps: list[str] = []
    cands: dict[str, dict] = {}
    ranked: list[str] = []
    crits: list[str] = []
    for e in events:
        k, p = e.get("kind"), e.get("payload", {})
        if k == "research_brief" and p.get("gap"):
            gaps.append(f"- {p.get('codename', '?')}: {p['gap']}")
        elif k == "candidate":
            cid = p.get("id") or p.get("codename") or p.get("title", "")
            cands[cid] = {
                "title": p.get("title", ""),
                "hypothesis": p.get("hypothesis", ""),
                "plan": p.get("experiment_plan", ""),
            }
        elif k == "recommendation":
            ranked = p.get("ranked") or ranked
        elif k == "discussion_message" and p.get("kind") == "critique":
            crits.append(
                f"- {p.get('from_codename')} → {p.get('targets')}: {p.get('content', '')[:160]}"
            )
    if not (gaps or cands):
        return None

    lines = [f"# Council findings — {topic}", ""]
    if ranked and ranked[0] in cands:
        w = cands[ranked[0]]
        lines += [
            "## Selected direction",
            f"**{w['title']}** — {w['hypothesis']}",
            "",
            f"Plan: {w['plan']}",
            "",
        ]
    if gaps:
        lines += ["## Gaps surfaced", *gaps, ""]
    if len(cands) > 1:
        lines += ["## Candidates considered", *[f"- {v['title']}" for v in cands.values()], ""]
    if crits:
        lines += ["## Key critiques", *crits[:6], ""]
    return Source(
        citekey=f"council:{run_id}",
        title=f"Council findings — {topic[:60]}",
        text="\n".join(lines),
        origin="internal",
    )


def preview(
    events: list[dict], papers_by_id: dict, cap: int = HARVEST_CAP
) -> tuple[int, bool, int]:
    """(#external, has_internal, #skipped) — for an opt-in prompt before spending tokens."""
    ext, skipped = collect_external(events, papers_by_id, cap)
    run_id, topic = run_meta(events)
    return len(ext), build_internal(events, topic, run_id) is not None, skipped


async def harvest_run(
    events: list[dict],
    papers_by_id: dict,
    ingestor,
    *,
    cap: int = HARVEST_CAP,
    on_step=None,
    on_ingest=None,
) -> HarvestReport:
    """on_step(done, total, label) → progress UI; on_ingest(source, IngestReport) → trace."""
    rep = HarvestReport()
    run_id, topic = run_meta(events)

    ext, rep.skipped_external = collect_external(events, papers_by_id, cap)
    internal = build_internal(events, topic, run_id)
    total = len(ext) + (1 if internal is not None else 0)
    done = 0

    for s in ext:
        if on_step:
            on_step(done, total, s.citekey)
        r = await ingestor.ingest(s)
        rep.external.append(s.citekey)
        if on_ingest:
            on_ingest(s, r)
        done += 1

    if internal is not None:
        if on_step:
            on_step(done, total, "council synthesis")
        r = await ingestor.ingest(internal)
        rep.internal = r.written + r.merged
        if on_ingest:
            on_ingest(internal, r)
        done += 1

    if on_step:
        on_step(done, total, "done")
    return rep
