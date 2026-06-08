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

from research_council import cli_ui as ui
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
    elif ev.kind == "candidate_revised":
        extra = f"v{p.get('version')} {p.get('title', '')[:48]}"
    elif ev.kind == "critique":
        extra = f"{p.get('axis')} sev{p.get('severity')} → {p.get('target_id')}"
    elif ev.kind == "verifier_signal":
        extra = f"{p.get('candidate_id')} feas={p.get('feasibility')} runnable={p.get('runnable')}"
    elif ev.kind == "discussion_message":
        addr = p.get("to") or p.get("targets")  # question→to, critique/revise→targets (==codename)
        tag = f"→@{addr}" if addr else ""
        extra = f"{p.get('from_codename')} [{p.get('kind')}{tag}] {p.get('content', '')[:60]}"
    elif ev.kind == "tool_call":
        extra = f"{p.get('codename')} ⟶ {p.get('tool')}({p.get('args', '')[:60]})"
    elif ev.kind == "setup":
        extra = f"seats={p.get('seats')} tools={p.get('tools')} facilitator={p.get('facilitator_model')}"
    elif ev.kind == "recommendation":
        extra = " > ".join(p.get("ranked", []))
    elif ev.kind == "usage_summary":
        t = p.get("totals", {})
        extra = f"${t.get('cost_usd', 0):.4f} · {t.get('input_tokens', 0)+t.get('output_tokens', 0)} tok · {t.get('tool_calls', 0)} tool calls"
    elif ev.kind == "human_action":
        extra = f"{p.get('action')} {p.get('feedback', '')}".strip()
    ui.stream_line(ev.round, ev.phase, ev.kind, ev.author_vendor, extra)


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
    ui.rule("① Models — one seat per vendor")
    i = 0
    while i < len(vendors):
        v = vendors[i]
        cur = cfg.seats[v]
        opts = choices_map.get(v) or [cur]
        if cur not in opts:
            opts = [cur, *opts]
        nav = ([] if i == 0 else [_BACK]) + [_EXIT]
        ans = ui.ask_select(f"{v} model", choices=[*opts, ui.sep(), *nav], default=cur)
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

    ui.rule("② Retrieval tools")
    picks = ui.ask_checkbox(
        "tools the council may use",
        choices=[questionary.Choice(t, checked=(t in cfg.tools)) for t in real_tools()],
    )
    if picks is None:
        raise typer.Abort()
    if picks:
        cfg.tools = picks


def _interactive_setup(cfg: RunConfig, live: bool) -> None:
    """Round-start wizard (plan/3 Q1): models (live) → tools → confirm hub.

    Cancel (Esc/Ctrl-C) or the ✗ exit option aborts the whole command.
    """
    vendors = [*cfg.seats] if live else []
    choices_map = _model_choices() if live else {}

    if live:
        _select_models(cfg, vendors, choices_map)
    _select_tools(cfg)

    while True:  # confirm hub — revise either selection, confirm, or exit
        ui.setup_summary(cfg.seats, cfg.tools, live=live,
                         facilitator=cfg.facilitator_model if live else None)
        actions = [_CONFIRM, _EDIT_TOOLS] + ([_EDIT_MODELS] if live else []) + [_EXIT]
        ans = ui.ask_select("ready?", choices=actions)
        if ans is None or ans == _EXIT:
            raise typer.Abort()
        if ans == _CONFIRM:
            break
        if ans == _EDIT_TOOLS:
            _select_tools(cfg)
        elif ans == _EDIT_MODELS:
            _select_models(cfg, vendors, choices_map)
    typer.echo("")


_GATE = {
    "iterate": "↻ iterate — another round, peers only",
    "amend": "✎ amend — another round + my note",
    "conclude": "✓ conclude — take the panel ranking",
    "select": "★ select — I pick the winner",
}


async def _cli_reviewer(rec: Recommendation, candidates: list[Candidate], rnd: int) -> ReviewAction:
    from research_council.debate.orchestrator_v2 import SAFETY_MAX_ROUNDS

    titles = {c.id: c.title for c in candidates}
    ui.console.print()
    ui.ranking_table(rec.ranked, rec.composites, titles, rec.breakdown,
                     title=f"round {rnd} · panel recommendation")

    # only offer another round while one is actually allowed (below the safety ceiling)
    can_iterate = rnd < SAFETY_MAX_ROUNDS
    label_to_action = {v: k for k, v in _GATE.items()}
    options = list(_GATE.values()) if can_iterate else [_GATE["conclude"], _GATE["select"]]
    if not can_iterate:
        ui.console.print(f"  [dim]round {rnd}/{SAFETY_MAX_ROUNDS} — safety ceiling reached; conclude or select.[/dim]")
    choice = await ui.ask_select_async("your call", choices=options)
    if choice is None:
        raise typer.Abort()
    action = label_to_action[choice]
    if action == "amend":
        note = (await ui.ask_text_async("amendment for next round")) or ""
        return ReviewAction(action="amend", feedback=note.strip())
    if action == "select":
        pick = await ui.ask_select_async(
            "winner", choices=[questionary_choice(cid, titles.get(cid, "")) for cid in rec.ranked])
        return ReviewAction(action="select", choice=pick or (rec.ranked[0] if rec.ranked else None))
    return ReviewAction(action=action)


def questionary_choice(cid: str, title: str):
    import questionary
    return questionary.Choice(title=f"{cid} — {title}", value=cid)


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
def ideate(
    topic: str = typer.Option(..., "--topic", "-t", help="research question / topic"),
    stage: str = typer.Option("ideation", help="lifecycle stage (config name)"),
    seats: str = typer.Option(None, help="vendor=model,... override"),
    tools: str = typer.Option(None, help="comma list, e.g. wiki,openalex,arxiv"),
    auto_iterate: int = typer.Option(1, "--auto-iterate", help="non-interactive: rounds to auto-iterate before concluding (capped at 8)"),
    live: bool = typer.Option(False, help="use real providers (needs keys + SDKs)"),
    harvest: bool = typer.Option(False, "--harvest", help="ingest each round into the LLM-wiki so later rounds read it (live; spends librarian tokens)"),
    stream: bool = typer.Option(True, help="print each phase event live"),
    interactive: bool = typer.Option(True, help="intake questions + review gate (TTY only)"),
):
    """v2 agentic ideation: intake → research → propose → deliberate → judge → gate.

    Interactively, rounds are human-driven (iterate/amend until you conclude/select, capped
    at a safety ceiling). Non-interactively, --auto-iterate sets how many rounds run unattended.
    """
    from research_council.debate.orchestrator_v2 import CODENAMES, run_ideation

    cfg = load_config(stage)
    if seats:
        cfg.seats = parse_seats(seats)
    if tools:
        cfg.tools = parse_tools(tools)

    tty = sys.stdin.isatty()
    if interactive and tty:
        _interactive_setup(cfg, live)  # arrow-key seat models (live) + retrieval tools

    retrieval = build_retrieval(cfg.tools) if live else build_stub_retrieval(cfg.tools)
    peers: dict = {}
    for vendor, model in cfg.seats.items():
        cn = CODENAMES.get(vendor, vendor)
        if live:
            from research_council.agents.agent_peer import AgentPeer, agent_model_name
            peers[cn] = AgentPeer(vendor, cn, agent_model_name(vendor, model), retrieval,
                                  max_iters=cfg.max_iters, max_tool_calls=cfg.max_tool_calls,
                                  price_model=model)
        else:
            from research_council.agents.stub_agent_peer import StubV2Peer
            peers[cn] = StubV2Peer(vendor, cn, retrieval)

    facilitator = None
    answer_fn = None
    if live:
        from research_council.agents.agent_peer import agent_model_name
        from research_council.agents.facilitator import Facilitator
        facilitator = Facilitator(agent_model_name("anthropic", cfg.facilitator_model),
                                  price_model=cfg.facilitator_model)
    if interactive and tty and facilitator is not None:
        async def answer_fn(q):  # noqa: E306
            ui.rule("③ Intake")
            return ((await ui.ask_text_async(q.question)) or "").strip()

    interactive_run = interactive and tty
    reviewer = _cli_reviewer if interactive_run else None
    rounds_label = "human-driven (cap 8)" if interactive_run else f"auto-iterate {auto_iterate}"
    trace = TraceWriter.new(cfg.stage)
    ui.banner("Research Council · ideation",
              f"{', '.join(f'{cn}={p.vendor}' for cn, p in peers.items())}  ·  "
              f"{'live' if live else 'offline'}  ·  rounds: {rounds_label}")

    # record the full setup as the first trace event (seats, tools, caps, facilitator)
    setup_ev = trace.emit("intake", "setup", {
        "seats": cfg.seats, "tools": cfg.tools, "live": live, "anonymize": cfg.anonymize,
        "facilitator_model": cfg.facilitator_model if facilitator is not None else None,
        "auto_iterate": auto_iterate, "interactive": interactive_run, "max_turns": cfg.max_turns,
        "max_iters": cfg.max_iters, "max_tool_calls": cfg.max_tool_calls,
    })
    if stream:
        _stream(setup_ev)

    # per-round wiki harvest (opt-in, live): each round's findings + cited papers are ingested
    # so the NEXT round's research can read them.
    on_round_end, librarian = None, None
    if harvest and live:
        from research_council.librarian.ingest import Ingestor
        librarian, _ = _build_librarian()
        on_round_end = _round_harvester(trace, retrieval, topic, librarian, Ingestor(librarian))
    elif harvest and not live:
        ui.console.print("[yellow]--harvest needs --live (the librarian uses real models); skipping harvest.[/yellow]")

    try:
        rec, candidates = asyncio.run(run_ideation(
            topic, peers, trace, facilitator=facilitator, answer_fn=answer_fn, reviewer=reviewer,
            weights=cfg.weights, auto_rounds=auto_iterate, max_turns=cfg.max_turns,
            anonymize_on=cfg.anonymize, on_round_end=on_round_end, emit=(_stream if stream else None),
        ))
    except KeyboardInterrupt:
        raise typer.Abort()

    titles = {c.id: c.title for c in candidates}
    ui.console.print()
    ui.ranking_table(rec.ranked, rec.composites, titles, rec.breakdown,
                     title="Final ranking — anonymized panel vote (self-scores excluded)")

    # cost / usage summary (live only; offline stubs don't spend)
    metered = [(cn, p) for cn, p in peers.items() if getattr(getattr(p, "usage", None), "requests", 0)]
    if metered:
        rows, total = [], 0.0
        for cn, p in metered:
            u = p.usage
            total += u.cost_usd
            rows.append((cn, u.cost_usd, u.input_tokens, u.output_tokens, u.requests, u.tool_calls))
        if facilitator is not None and getattr(facilitator.usage, "requests", 0):
            fu = facilitator.usage
            total += fu.cost_usd
            rows.append(("facilitator", fu.cost_usd, fu.input_tokens, fu.output_tokens, fu.requests, None))
        hits, misses = getattr(retrieval, "hits", 0), getattr(retrieval, "misses", 0)
        ui.console.print()
        ui.usage_table(rows, cache=(hits, misses) if (hits or misses) else None, total=total)

    if librarian is not None and librarian.usage.cost_usd:
        ui.console.print(f"  [dim]wiki harvest total · librarian ${librarian.usage.cost_usd:.4f}[/dim]")
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


def _build_librarian():
    """The Sonnet librarian configured from wiki.yaml. Returns (librarian, model_id)."""
    import yaml

    from research_council.agents.agent_peer import agent_model_name
    from research_council.config import CONFIG_DIR
    from research_council.librarian.router import Librarian

    wcfg = yaml.safe_load((CONFIG_DIR / "wiki.yaml").read_text()) or {}
    m = wcfg.get("model", "claude-sonnet-4-6")
    return Librarian(agent_model_name("anthropic", m), price_model=m), m


def _round_harvester(trace, retrieval, topic, librarian, ingestor):
    """Build an async on_round_end(rnd) that ingests THIS round into the wiki, so the next
    round's research reads it. External cited papers + the round's internal synthesis."""
    import json

    from research_council.librarian.harvest import build_internal, collect_external

    async def on_round_end(rnd: int) -> None:
        events = [json.loads(ln) for ln in trace.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        this_round = [e for e in events if e.get("round") in (0, rnd)]  # round-0 carries the topic
        papers = retrieval.cached_papers() if hasattr(retrieval, "cached_papers") else {}
        sources = collect_external(this_round, papers)[0]
        internal = build_internal(this_round, topic, f"{trace.run_id}-r{rnd}")
        if internal is not None:
            sources.append(internal)
        n = 0
        for s in sources:
            r = await ingestor.ingest(s)
            trace.emit("harvest", "librarian_ingest",
                       {"round": rnd, "citekey": s.citekey, "origin": s.origin,
                        "written": r.written, "merged": r.merged})
            n += len(r.written) + len(r.merged)
        if sources:
            ui.console.print(f"  [dim]wiki ← round {rnd}: {len(sources)} source(s) → {n} page(s) · "
                             f"librarian ${librarian.usage.cost_usd:.4f}[/dim]")

    return on_round_end


@app.command()
def ingest(
    path: str = typer.Argument(..., help="local text/markdown file to ingest into the wiki"),
    title: str = typer.Option(None, help="page title (default: first heading or filename)"),
):
    """Librarian write side: route a source into the LLM-wiki (needs an Anthropic key via mise)."""
    from research_council.librarian.ingest import Ingestor
    from research_council.librarian.schema import Source

    src_path = Path(path)
    if not src_path.exists():
        typer.echo(f"no such file: {path}")
        raise typer.Exit(1)
    text = src_path.read_text(encoding="utf-8", errors="ignore")
    if not title:
        heads = [ln.lstrip("# ").strip() for ln in text.splitlines() if ln.lstrip().startswith("#")]
        title = heads[0] if heads else src_path.stem.replace("-", " ")

    librarian, model_id = _build_librarian()
    source = Source(citekey=f"manual:{src_path.stem}", title=title, text=text, origin="external")

    ui.console.print(f"ingesting [bold]{title}[/bold] via [cyan]{model_id}[/cyan] …")
    with ui.spinner(f"librarian routing '{title}'"):
        rep = asyncio.run(Ingestor(librarian).ingest(source))
    for rp in rep.written:
        ui.console.print(f"  [green]+[/green] {rp}")
    for rp in rep.merged:
        ui.console.print(f"  [yellow]~[/yellow] {rp} [dim](merged)[/dim]")
    if rep.raw_saved:
        ui.console.print(f"  [dim]raw → {rep.raw_saved}[/dim]")
    u = librarian.usage
    ui.console.print(f"  [dim]cost ≈ ${u.cost_usd:.4f} · {u.input_tokens}+{u.output_tokens} tok · "
                     f"audit → knowledge/wiki/log.md[/dim]")


@app.command()
def lint(
    semantic: bool = typer.Option(False, help="also run an LLM contradiction/gap audit (needs key)"),
):
    """Audit the LLM-wiki: broken links, orphans, index drift, empty pages (+ optional LLM audit)."""
    import datetime

    from research_council.librarian.lint import append_lint_log, lint_structure

    rep = lint_structure()
    typer.echo(f"wiki: {rep.pages} pages · {len(rep.issues)} issue(s)")
    for kind, items in sorted(rep.by_kind().items()):
        typer.echo(f"  {kind} ({len(items)}):")
        for i in items[:20]:
            typer.echo(f"    {i.page}" + (f" — {i.detail}" if i.detail else ""))
    if not rep.issues:
        typer.echo("  clean ✓")

    if semantic:
        from research_council.librarian.lint import lint_semantic
        librarian, model_id = _build_librarian()
        typer.echo(f"\nsemantic audit via {model_id} …")
        audit = asyncio.run(lint_semantic(f"anthropic:{model_id}"))
        for label, items in (("contradictions", audit.contradictions), ("gaps", audit.gaps)):
            typer.echo(f"  {label} ({len(items)}):")
            for s in items:
                typer.echo(f"    - {s}")

    append_lint_log(None, rep, datetime.date.today().isoformat())


wiki_app = typer.Typer(help="Manage the LLM-wiki library (human-triggered: archive / reset / restore).")
app.add_typer(wiki_app, name="wiki")


def _stamp() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y%m%dT%H%M%S")


@wiki_app.command("list")
def wiki_list():
    """List wiki archives (newest first)."""
    from research_council.librarian.archive import list_archives

    arcs = list_archives()
    if not arcs:
        ui.info("no archives yet.")
        return
    ui.rule(f"{len(arcs)} archive(s)")
    for a in arcs:
        ui.console.print(f"  [cyan]{a}[/cyan]")


@wiki_app.command("archive")
def wiki_archive():
    """Snapshot the current library to .archive/<timestamp>/ (keeps the working copy)."""
    from research_council.librarian.archive import archive_library

    stamp = _stamp()
    archive_library(None, stamp)
    ui.console.print(f"archived → [cyan].archive/{stamp}[/cyan]")


@wiki_app.command("reset")
def wiki_reset(hard: bool = typer.Option(False, "--hard", help="wipe WITHOUT archiving first")):
    """Clear the wiki back to empty. Archives first unless --hard."""
    from research_council.librarian.archive import reset_library

    token = "wipe" if hard else "reset"
    note = "[red bold]HARD wipe — no archive kept[/red bold]" if hard else "archives first, then clears"
    ui.console.print(f"reset: {note}")
    if (ui.ask_text(f"type '{token}' to confirm") or "").strip() != token:
        ui.info("aborted.")
        raise typer.Abort()
    archived = reset_library(None, _stamp(), hard=hard)
    ui.console.print("wiki cleared." + (f" backup → [cyan].archive/{archived}[/cyan]" if archived else ""))


@wiki_app.command("restore")
def wiki_restore(stamp: str = typer.Argument(None, help="archive timestamp (omit to pick interactively)")):
    """Restore a previous archive over the current wiki (current is backed up first)."""
    from research_council.librarian.archive import list_archives, restore_library

    arcs = list_archives()
    if not arcs:
        ui.info("no archives to restore.")
        raise typer.Exit(1)
    if not stamp:
        stamp = ui.ask_select("restore which archive?", choices=arcs)
        if not stamp:
            raise typer.Abort()
    if stamp not in arcs:
        ui.info(f"no such archive: {stamp}")
        raise typer.Exit(1)
    if (ui.ask_text(f"type 'restore' to overwrite the current wiki with {stamp}") or "").strip() != "restore":
        ui.info("aborted.")
        raise typer.Abort()
    backup = f"pre-restore-{_stamp()}"
    restore_library(None, stamp, backup_stamp=backup)
    ui.console.print(f"restored [cyan]{stamp}[/cyan] · current backed up → [cyan].archive/{backup}[/cyan]")


project_app = typer.Typer(help="Macro lifecycle: ideation → experimentation → writing (human-gated).")
app.add_typer(project_app, name="project")

_STAGE_ICON = {"approved": "[green]✓ approved[/green]", "awaiting_approval": "[yellow]◐ awaiting approval[/yellow]",
               "active": "[blue]▶ active[/blue]", "pending": "[dim]○ pending[/dim]"}


def _proj_slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:28] or "project"


@project_app.command("new")
def project_new(
    topic: str = typer.Option(..., "--topic", "-t", help="research question"),
    live: bool = typer.Option(False, help="use real providers (needs keys)"),
    interactive: bool = typer.Option(True, help="review gate during ideation (TTY only)"),
    auto_iterate: int = typer.Option(1, "--auto-iterate", help="non-interactive ideation rounds"),
    seats: str = typer.Option(None, help="vendor=model,... override"),
    tools: str = typer.Option(None, help="retrieval tools override"),
):
    """Start a project: run Stage A (ideation), then await your approval to advance to B."""
    import datetime

    from research_council.debate.orchestrator_v2 import CODENAMES, run_ideation
    from research_council.lifecycle import ProjectStore, new_project, record_result

    cfg = load_config("ideation")
    if seats:
        cfg.seats = parse_seats(seats)
    if tools:
        cfg.tools = parse_tools(tools)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    pid = f"{_proj_slug(topic)}-{stamp[-6:]}"
    store = ProjectStore()
    proj = new_project(topic, pid, created=stamp)
    store.save(proj)
    ui.banner(f"Project · {pid}", f"{topic}  ·  stage ① ideation  ·  {'live' if live else 'offline'}")

    retrieval = build_retrieval(cfg.tools) if live else build_stub_retrieval(cfg.tools)
    peers: dict = {}
    for vendor, model in cfg.seats.items():
        cn = CODENAMES.get(vendor, vendor)
        if live:
            from research_council.agents.agent_peer import AgentPeer, agent_model_name
            peers[cn] = AgentPeer(vendor, cn, agent_model_name(vendor, model), retrieval,
                                  max_iters=cfg.max_iters, max_tool_calls=cfg.max_tool_calls, price_model=model)
        else:
            from research_council.agents.stub_agent_peer import StubV2Peer
            peers[cn] = StubV2Peer(vendor, cn, retrieval)

    reviewer = _cli_reviewer if (interactive and sys.stdin.isatty()) else None
    trace = TraceWriter.new("ideation")
    try:
        rec, candidates = asyncio.run(run_ideation(
            topic, peers, trace, reviewer=reviewer, auto_rounds=auto_iterate,
            max_turns=cfg.max_turns, anonymize_on=cfg.anonymize, emit=_stream))
    except KeyboardInterrupt:
        raise typer.Abort()

    by = {c.id: c for c in candidates}
    winner = by.get(rec.ranked[0]) if rec.ranked else None
    if winner is None:
        ui.console.print("[red]ideation produced no candidate[/red]")
        raise typer.Exit(1)
    record_result(proj, "ideation", run_id=trace.run_id, summary=winner.title,
                  artifacts={"idea": winner.model_dump(), "experiment_plan": winner.experiment_plan})
    store.save(proj)
    ui.console.print(f"\n[green]Stage A complete[/green] · selected: [bold]{winner.title}[/bold]")
    ui.console.print(f"  approve & advance → [cyan]council project approve {pid}[/cyan]   ·   "
                     f"status → council project status {pid}")


@project_app.command("status")
def project_status(pid: str = typer.Argument(..., help="project id")):
    """Show a project's stages and where it is."""
    from rich import box
    from rich.table import Table

    from research_council.lifecycle import ProjectStore, is_complete
    from research_council.store.models import STAGES

    store = ProjectStore()
    if not store.exists(pid):
        ui.info(f"no project {pid!r}")
        raise typer.Exit(1)
    p = store.load(pid)
    ui.banner(f"Project · {p.id}", p.topic)
    t = Table(box=box.SIMPLE_HEAVY)
    t.add_column("#", style="dim")
    t.add_column("stage", style="cyan")
    t.add_column("status")
    t.add_column("outcome")
    for i, n in enumerate(STAGES):
        s = p.stages[n]
        t.add_row("①②③"[i], n, _STAGE_ICON.get(s.status, s.status), s.summary[:64])
    ui.console.print(t)
    if is_complete(p):
        ui.console.print("[green]✓ project complete[/green]")
    elif p.stages[p.current].status == "awaiting_approval":
        ui.console.print(f"next: [cyan]council project approve {pid}[/cyan]")


@project_app.command("list")
def project_list():
    """List all projects."""
    from research_council.lifecycle import ProjectStore

    store = ProjectStore()
    ids = store.list()
    if not ids:
        ui.info("no projects yet.")
        return
    ui.rule(f"{len(ids)} project(s)")
    for pid in ids:
        p = store.load(pid)
        ui.console.print(f"  [cyan]{pid}[/cyan] · stage {p.current} · {p.topic[:54]}")


@project_app.command("approve")
def project_approve(pid: str = typer.Argument(..., help="project id")):
    """Approve the current stage and advance (running the next stage's engine)."""
    from research_council.lifecycle import (
        ProjectStore,
        approve_and_advance,
        record_result,
        run_stage_stub,
    )

    store = ProjectStore()
    if not store.exists(pid):
        ui.info(f"no project {pid!r}")
        raise typer.Exit(1)
    p = store.load(pid)
    cur = p.current
    if p.stages[cur].status != "awaiting_approval":
        ui.info(f"stage '{cur}' is {p.stages[cur].status} — nothing to approve.")
        raise typer.Exit(1)
    p, handoff = approve_and_advance(p)
    if handoff is not None:  # advanced to a next stage → run it (B/C are stubs for now)
        nxt = p.current
        summary, artifacts = run_stage_stub(nxt, handoff)
        record_result(p, nxt, summary=summary, artifacts=artifacts)
        ui.console.print(f"[green]approved {cur}[/green] → ▶ [cyan]{nxt}[/cyan]\n  {summary}")
        ui.console.print(f"  next: [cyan]council project approve {pid}[/cyan]")
    else:
        ui.console.print(f"[green]approved {cur}[/green] · [bold]project complete 🎉[/bold]")
    store.save(p)


if __name__ == "__main__":
    app()
