"""`rc` CLI. Offline (stub) debate by default; --live uses real providers.

Streams each phase event live (watch the debate) and opens an interactive review
gate at each round when stdin is a TTY: proceed / accept <id> / comment → re-run.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import typer

from research_council.agents.stub_peer import StubPeer
from research_council.config import load_config, parse_seats, parse_tools
from research_council.debate.orchestrator import run_debate
from research_council.retrieval.registry import build_retrieval, build_stub_retrieval
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import Candidate, Event, Recommendation, ReviewAction, RunConfig
from research_council.verify.mock import MockVerifier

app = typer.Typer(add_completion=False, help="research-council: cross-vendor AI4SE debate")


def _build_peers(cfg: RunConfig, live: bool):
    if not live:
        return [StubPeer(v) for v in cfg.seats]
    from research_council.agents.llm_peer import LLMPeer
    from research_council.providers.sdk import build_provider
    return [LLMPeer(v, build_provider(v, m)) for v, m in cfg.seats.items()]


def _stream(ev: Event) -> None:
    p = ev.payload
    extra = ""
    if ev.kind == "research_brief":
        extra = p.get("gap", "")[:64]
    elif ev.kind == "candidate":
        extra = p.get("title", "")[:64]
    elif ev.kind == "critique":
        extra = f"{p.get('axis')} sev{p.get('severity')} → {p.get('target_id')}"
    elif ev.kind == "verifier_signal":
        extra = f"{p.get('candidate_id')} feas={p.get('feasibility')} runnable={p.get('runnable')}"
    elif ev.kind == "recommendation":
        extra = " > ".join(p.get("ranked", []))
    elif ev.kind == "human_action":
        extra = f"{p.get('action')} {p.get('feedback', '')}".strip()
    who = f" {ev.author_vendor}" if ev.author_vendor else ""
    typer.echo(f"  r{ev.round} [{ev.phase}] {ev.kind}{who}  {extra}".rstrip())


def _parse_model_choices(text: str) -> dict[str, list[str]]:
    """Parse mise.toml comments: RC_<VENDOR>_MODEL = "x" # [ a | b | c ] -> {vendor:[a,b,c]}."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'RC_(\w+?)_MODEL\s*=\s*"[^"]*"\s*#\s*\[([^\]]*)\]', text):
        out[m.group(1).lower()] = [c.strip() for c in m.group(2).split("|") if c.strip()]
    return out


def _model_choices() -> dict[str, list[str]]:
    p = Path("mise.toml")
    return _parse_model_choices(p.read_text(encoding="utf-8")) if p.exists() else {}


_BACK = "← back"
_EXIT = "✗ exit"
_CONFIRM = "✓ confirm & start"
_EDIT_TOOLS = "↩ edit tools"
_EDIT_MODELS = "↩ edit models"


def _select_models(cfg: RunConfig, vendors: list[str], choices_map: dict[str, list[str]]) -> None:
    """Linear model picker with ← back / ✗ exit. Aborts the program on exit/cancel."""
    import questionary

    i = 0
    while i < len(vendors):
        v = vendors[i]
        cur = cfg.seats[v]
        opts = choices_map.get(v) or [cur]
        if cur not in opts:
            opts = [cur, *opts]
        nav = ([] if i == 0 else [_BACK]) + [_EXIT]
        ans = questionary.select(
            f"{v} model", choices=[*opts, questionary.Separator(), *nav], default=cur
        ).ask()
        if ans is None or ans == _EXIT:
            raise typer.Abort()
        if ans == _BACK:
            i -= 1
            continue
        cfg.seats[v] = ans
        i += 1


def _select_tools(cfg: RunConfig) -> None:
    import questionary
    from research_council.retrieval.registry import real_tools

    picks = questionary.checkbox(
        "retrieval tools (↑↓ move · space toggle · enter continue)",
        choices=[questionary.Choice(t, checked=(t in cfg.tools)) for t in real_tools()],
    ).ask()
    if picks is None:
        raise typer.Abort()
    if picks:
        cfg.tools = picks


def _interactive_setup(cfg: RunConfig, live: bool) -> None:
    """Round-start wizard (plan/3 Q1): models (live) → tools → confirm hub.

    Cancel (Esc/Ctrl-C) or the ✗ exit option aborts the whole command.
    """
    import questionary

    vendors = [*cfg.seats] if live else []
    choices_map = _model_choices() if live else {}

    if live:
        _select_models(cfg, vendors, choices_map)
    _select_tools(cfg)

    while True:  # confirm hub — revise either selection, confirm, or exit
        summary = (f"models {cfg.seats} · " if live else "") + f"tools {cfg.tools}"
        actions = [_CONFIRM, _EDIT_TOOLS] + ([_EDIT_MODELS] if live else []) + [_EXIT]
        ans = questionary.select(f"Ready?  {summary}", choices=actions).ask()
        if ans is None or ans == _EXIT:
            raise typer.Abort()
        if ans == _CONFIRM:
            break
        if ans == _EDIT_TOOLS:
            _select_tools(cfg)
        elif ans == _EDIT_MODELS:
            _select_models(cfg, vendors, choices_map)
    typer.echo("")


async def _cli_reviewer(rec: Recommendation, candidates: list[Candidate], rnd: int) -> ReviewAction:
    titles = {c.id: c.title for c in candidates}
    typer.echo(f"\n— round {rnd} recommendation —")
    for i, cid in enumerate(rec.ranked, 1):
        typer.echo(f"  {i}. {rec.composites[cid]:.3f}  {cid}: {titles.get(cid, '')}")
    resp = input("gate [i]terate / [a]mend / [c]onclude / [s]elect <id>: ").strip()
    k = resp[:1].lower()
    if k == "s":
        parts = resp.split(maxsplit=1)
        return ReviewAction(action="select", choice=parts[1] if len(parts) > 1 else None)
    if k == "a":
        return ReviewAction(action="amend", feedback=input("  amendment for next round: ").strip())
    if k == "c":
        return ReviewAction(action="conclude")
    return ReviewAction(action="iterate")  # default / [i]


@app.command()
def debate(
    topic: str = typer.Option(..., "--topic", "-t", help="research question / topic"),
    stage: str = typer.Option("ideation", help="lifecycle stage (config name)"),
    seats: str = typer.Option(None, help="vendor=model,... override"),
    tools: str = typer.Option(None, help="comma list, e.g. wiki,openalex,arxiv"),
    rounds: int = typer.Option(None, help="max debate rounds"),
    anonymize: bool = typer.Option(None, help="anonymize authorship (bias control)"),
    live: bool = typer.Option(False, help="use real providers (needs keys + SDKs)"),
    stream: bool = typer.Option(True, help="print each phase event live"),
    interactive: bool = typer.Option(True, help="open the review gate each round (TTY only)"),
):
    cfg = load_config(stage)
    if seats:
        cfg.seats = parse_seats(seats)
    if tools:
        cfg.tools = parse_tools(tools)
    if rounds is not None:
        cfg.n_rounds = rounds
    if anonymize is not None:
        cfg.anonymize = anonymize

    # Round-start interactive selection (TTY only; CI/non-tty uses config as-is).
    if interactive and sys.stdin.isatty():
        _interactive_setup(cfg, live)

    peers = _build_peers(cfg, live)
    # Real retrieval (network) on --live; offline runs stay network-free with stubs.
    retrieval = build_retrieval(cfg.tools) if live else build_stub_retrieval(cfg.tools)
    verifier = MockVerifier()
    trace = TraceWriter.new(cfg.stage)

    typer.echo(f"seats  {cfg.seats}")
    typer.echo(f"tools  {cfg.tools}   anonymize={cfg.anonymize}   rounds={cfg.n_rounds}\n")

    emit = _stream if stream else None
    reviewer = _cli_reviewer if (interactive and sys.stdin.isatty()) else None

    try:
        rec, candidates = asyncio.run(
            run_debate(cfg, topic, peers, retrieval, verifier, trace, emit=emit, reviewer=reviewer)
        )
    except KeyboardInterrupt:
        raise typer.Abort()

    titles = {c.id: c.title for c in candidates}
    typer.echo("\nFinal ranking (verifier-weighted panel vote):")
    for i, cid in enumerate(rec.ranked, 1):
        typer.echo(f"  {i}. {rec.composites[cid]:.3f}  {titles.get(cid, cid)}")
    typer.echo(f"\ntrace: {trace.path}")


@app.command()
def search(
    query: str = typer.Argument(..., help="search query"),
    tools: str = typer.Option("openalex,arxiv", help="comma list of sources"),
    k: int = typer.Option(8, help="results per source"),
):
    """Run real retrieval and print results. Free (no LLM tokens), hits source APIs."""
    retrieval = build_retrieval([t.strip() for t in tools.split(",") if t.strip()])
    papers = asyncio.run(retrieval.search(query, k))
    typer.echo(f"{len(papers)} results for {query!r}:\n")
    for p in papers:
        typer.echo(f"  [{p.source:10}] {p.year or '----'}  {p.title[:78]}")
    if not papers:
        typer.echo("  (none — sources returned nothing or are unreachable)")


@app.command()
def check(stage: str = typer.Option("ideation", help="config to read seats/models from")):
    """Ping each provider seat with a tiny prompt. Light cost (~cents). Diagnoses --live."""
    cfg = load_config(stage)
    from research_council.providers.sdk import build_provider

    async def ping(vendor: str, model: str):
        try:
            p = build_provider(vendor, model)
            r = await p.complete("Reply with exactly: OK", "ping", kind="check")
            return vendor, model, "OK", r.usage.cost_usd, (r.text or "").strip()[:40]
        except Exception as e:  # surface the real failure per provider
            return vendor, model, f"ERROR {type(e).__name__}", 0.0, str(e)[:140]

    async def run():
        return await asyncio.gather(*(ping(v, m) for v, m in cfg.seats.items()))

    typer.echo("Pinging providers (one tiny call each)...\n")
    for vendor, model, status, cost, detail in asyncio.run(run()):
        typer.echo(f"  {vendor:10} {model:24} {status:14} ${cost:.5f}  {detail}")


@app.command()
def resume(run_id: str):  # pragma: no cover
    typer.echo(f"resume {run_id}: TODO (checkpoint replay) — increment.")


@app.command()
def ingest(path: str):  # pragma: no cover
    typer.echo(f"ingest {path}: TODO (wiki write side / librarian) — see plan/9.")


@app.command()
def lint():  # pragma: no cover
    typer.echo("lint: TODO (wiki contradiction/orphan/gap audit) — see plan/9.")


if __name__ == "__main__":
    app()
