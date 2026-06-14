"""`rc` CLI. Offline (stub) debate by default; --live uses real providers.

Streams each phase event live (watch the debate) and opens an interactive review
gate at each round when stdin is a TTY: proceed / accept <id> / comment → re-run.
"""

from __future__ import annotations

import asyncio
import os
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
        rc = p.get("result_chars")
        size = "" if rc is None else (" → ∅ empty" if p.get("empty") else f" → {rc}c")
        extra = f"{p.get('codename')} ⟶ {p.get('tool')}({p.get('args', '')[:60]}){size}"
    elif ev.kind == "setup":
        extra = f"seats={p.get('seats')} tools={p.get('tools')} facilitator={p.get('facilitator_model')}"
    elif ev.kind == "recommendation":
        extra = " > ".join(p.get("ranked", []))
    elif ev.kind == "usage_summary":
        t = p.get("totals", {})
        extra = f"${t.get('cost_usd', 0):.4f} · {t.get('input_tokens', 0) + t.get('output_tokens', 0)} tok · {t.get('tool_calls', 0)} tool calls"
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


# Retrieval providers that need an API key/token; flagged in the picker so you don't enable a
# silently-dead source (HybridRetrieval isolates failures, so a keyless provider just returns
# nothing). wiki/openalex/arxiv work with no key.
_TOOL_KEY_HINT = {
    "github": ("GITHUB_TOKEN", "needs GITHUB_TOKEN"),
    "semanticscholar": (
        "SEMANTIC_SCHOLAR_API_KEY",
        "rate-limited without SEMANTIC_SCHOLAR_API_KEY",
    ),
}


def _tool_label(t: str) -> str:
    env, hint = _TOOL_KEY_HINT.get(t, ("", ""))
    return f"{t}  ⚠ {hint}" if env and not os.getenv(env) else t


def _select_tools(cfg: RunConfig) -> None:
    import questionary

    from research_council.retrieval.registry import real_tools

    ui.rule("② Retrieval tools")
    picks = ui.ask_checkbox(
        "tools the council may use",
        choices=[
            questionary.Choice(_tool_label(t), value=t, checked=(t in cfg.tools))
            for t in real_tools()
        ],
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
        ui.setup_summary(
            cfg.seats, cfg.tools, live=live, facilitator=cfg.facilitator_model if live else None
        )
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
    ui.ranking_table(
        rec.ranked,
        rec.composites,
        titles,
        rec.breakdown,
        title=f"round {rnd} · panel recommendation",
    )

    # only offer another round while one is actually allowed (below the safety ceiling)
    can_iterate = rnd < SAFETY_MAX_ROUNDS
    label_to_action = {v: k for k, v in _GATE.items()}
    options = list(_GATE.values()) if can_iterate else [_GATE["conclude"], _GATE["select"]]
    if not can_iterate:
        ui.console.print(
            f"  [dim]round {rnd}/{SAFETY_MAX_ROUNDS} — safety ceiling reached; conclude or select.[/dim]"
        )
    choice = await ui.ask_select_async("your call", choices=options)
    if choice is None:
        raise typer.Abort()
    action = label_to_action[choice]
    if action == "amend":
        note = (await ui.ask_text_async("amendment for next round")) or ""
        return ReviewAction(action="amend", feedback=note.strip())
    if action == "select":
        pick = await ui.ask_select_async(
            "winner", choices=[questionary_choice(cid, titles.get(cid, "")) for cid in rec.ranked]
        )
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
        raise typer.Abort() from None

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
    auto_iterate: int = typer.Option(
        1,
        "--auto-iterate",
        help="non-interactive: rounds to auto-iterate before concluding (capped at 8)",
    ),
    live: bool = typer.Option(False, help="use real providers (needs keys + SDKs)"),
    harvest: bool = typer.Option(
        False,
        "--harvest",
        help="ingest each round into the LLM-wiki so later rounds read it (live; spends librarian tokens)",
    ),
    stream: bool = typer.Option(True, help="print each phase event live"),
    interactive: bool = typer.Option(True, help="onboarding questions + review gate (TTY only)"),
):
    """v2 agentic ideation: onboarding → research → propose → deliberate → judge → gate.

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

            peers[cn] = AgentPeer(
                vendor,
                cn,
                agent_model_name(vendor, model),
                retrieval,
                max_iters=cfg.max_iters,
                max_tool_calls=cfg.max_tool_calls,
                price_model=model,
            )
        else:
            from research_council.agents.stub_agent_peer import StubV2Peer

            peers[cn] = StubV2Peer(vendor, cn, retrieval)

    facilitator = None
    answer_fn = None
    if live:
        from research_council.agents.agent_peer import agent_model_name
        from research_council.agents.facilitator import Facilitator

        facilitator = Facilitator(
            agent_model_name("anthropic", cfg.facilitator_model), price_model=cfg.facilitator_model
        )
    if interactive and tty and facilitator is not None:

        async def answer_fn(q):  # noqa: E306
            ui.rule("③ Onboarding")
            return ((await ui.ask_text_async(q.question)) or "").strip()

    interactive_run = interactive and tty
    reviewer = _cli_reviewer if interactive_run else None
    rounds_label = "human-driven (cap 8)" if interactive_run else f"auto-iterate {auto_iterate}"
    trace = TraceWriter.new(cfg.stage)
    ui.banner(
        "Research Council · ideation",
        f"{', '.join(f'{cn}={p.vendor}' for cn, p in peers.items())}  ·  "
        f"{'live' if live else 'offline'}  ·  rounds: {rounds_label}",
    )

    # record the full setup as the first trace event (seats, tools, caps, facilitator)
    setup_ev = trace.emit(
        "onboarding",
        "setup",
        {
            "seats": cfg.seats,
            "tools": cfg.tools,
            "live": live,
            "anonymize": cfg.anonymize,
            "facilitator_model": cfg.facilitator_model if facilitator is not None else None,
            "auto_iterate": auto_iterate,
            "interactive": interactive_run,
            "max_turns": cfg.max_turns,
            "max_iters": cfg.max_iters,
            "max_tool_calls": cfg.max_tool_calls,
        },
    )
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
        ui.console.print(
            "[yellow]--harvest needs --live (the librarian uses real models); skipping harvest.[/yellow]"
        )

    try:
        rec, candidates = asyncio.run(
            run_ideation(
                topic,
                peers,
                trace,
                facilitator=facilitator,
                answer_fn=answer_fn,
                reviewer=reviewer,
                weights=cfg.weights,
                auto_rounds=auto_iterate,
                max_turns=cfg.max_turns,
                max_msgs_per_peer=cfg.max_msgs_per_peer,
                anonymize_on=cfg.anonymize,
                on_round_end=on_round_end,
                emit=(_stream if stream else None),
            )
        )
    except KeyboardInterrupt:
        raise typer.Abort() from None

    titles = {c.id: c.title for c in candidates}
    ui.console.print()
    ui.ranking_table(
        rec.ranked,
        rec.composites,
        titles,
        rec.breakdown,
        title="Final ranking — anonymized panel vote (self-scores excluded)",
    )

    # cost / usage summary (live only; offline stubs don't spend)
    metered = [
        (cn, p) for cn, p in peers.items() if getattr(getattr(p, "usage", None), "requests", 0)
    ]
    if metered:
        rows, total = [], 0.0
        for cn, p in metered:
            u = p.usage
            total += u.cost_usd
            rows.append((cn, u.cost_usd, u.input_tokens, u.output_tokens, u.requests, u.tool_calls))
        if facilitator is not None and getattr(facilitator.usage, "requests", 0):
            fu = facilitator.usage
            total += fu.cost_usd
            rows.append(
                ("facilitator", fu.cost_usd, fu.input_tokens, fu.output_tokens, fu.requests, None)
            )
        hits, misses = getattr(retrieval, "hits", 0), getattr(retrieval, "misses", 0)
        ui.console.print()
        ui.usage_table(rows, cache=(hits, misses) if (hits or misses) else None, total=total)

    if librarian is not None and librarian.usage.cost_usd:
        ui.console.print(
            f"  [dim]wiki harvest total · librarian ${librarian.usage.cost_usd:.4f}[/dim]"
        )
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
        events = [
            json.loads(ln)
            for ln in trace.path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        this_round = [e for e in events if e.get("round") in (0, rnd)]  # round-0 carries the topic
        papers = retrieval.cached_papers() if hasattr(retrieval, "cached_papers") else {}
        sources = collect_external(this_round, papers)[0]
        internal = build_internal(this_round, topic, f"{trace.run_id}-r{rnd}")
        if internal is not None:
            sources.append(internal)
        n = 0
        for s in sources:
            r = await ingestor.ingest(s)
            trace.emit(
                "harvest",
                "librarian_ingest",
                {
                    "round": rnd,
                    "citekey": s.citekey,
                    "origin": s.origin,
                    "written": r.written,
                    "merged": r.merged,
                },
            )
            n += len(r.written) + len(r.merged)
        if sources:
            ui.console.print(
                f"  [dim]wiki ← round {rnd}: {len(sources)} source(s) → {n} page(s) · "
                f"librarian ${librarian.usage.cost_usd:.4f}[/dim]"
            )

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
    ui.console.print(
        f"  [dim]cost ≈ ${u.cost_usd:.4f} · {u.input_tokens}+{u.output_tokens} tok · "
        f"audit → knowledge/wiki/log.md[/dim]"
    )


@app.command()
def lint(
    semantic: bool = typer.Option(
        False, help="also run an LLM contradiction/gap audit (needs key)"
    ),
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


wiki_app = typer.Typer(
    help="Manage the LLM-wiki library (human-triggered: archive / reset / restore)."
)
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
    note = (
        "[red bold]HARD wipe — no archive kept[/red bold]"
        if hard
        else "archives first, then clears"
    )
    ui.console.print(f"reset: {note}")
    if (ui.ask_text(f"type '{token}' to confirm") or "").strip() != token:
        ui.info("aborted.")
        raise typer.Abort()
    archived = reset_library(None, _stamp(), hard=hard)
    ui.console.print(
        "wiki cleared." + (f" backup → [cyan].archive/{archived}[/cyan]" if archived else "")
    )


@wiki_app.command("restore")
def wiki_restore(
    stamp: str = typer.Argument(None, help="archive timestamp (omit to pick interactively)"),
):
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
    if (
        ui.ask_text(f"type 'restore' to overwrite the current wiki with {stamp}") or ""
    ).strip() != "restore":
        ui.info("aborted.")
        raise typer.Abort()
    backup = f"pre-restore-{_stamp()}"
    restore_library(None, stamp, backup_stamp=backup)
    ui.console.print(
        f"restored [cyan]{stamp}[/cyan] · current backed up → [cyan].archive/{backup}[/cyan]"
    )


project_app = typer.Typer(
    help="Macro lifecycle: ideation → experimentation → writing (human-gated)."
)
app.add_typer(project_app, name="project")

_STAGE_ICON = {
    "approved": "[green]✓ approved[/green]",
    "awaiting_approval": "[yellow]◐ awaiting approval[/yellow]",
    "active": "[blue]▶ active[/blue]",
    "pending": "[dim]○ pending[/dim]",
}


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
    from research_council.lifecycle import ProjectStore, new_project

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
    ui.banner(
        f"Project · {pid}", f"{topic}  ·  stage ① ideation  ·  {'live' if live else 'offline'}"
    )

    retrieval = build_retrieval(cfg.tools) if live else build_stub_retrieval(cfg.tools)
    peers: dict = {}
    for vendor, model in cfg.seats.items():
        cn = CODENAMES.get(vendor, vendor)
        if live:
            from research_council.agents.agent_peer import AgentPeer, agent_model_name

            peers[cn] = AgentPeer(
                vendor,
                cn,
                agent_model_name(vendor, model),
                retrieval,
                max_iters=cfg.max_iters,
                max_tool_calls=cfg.max_tool_calls,
                price_model=model,
            )
        else:
            from research_council.agents.stub_agent_peer import StubV2Peer

            peers[cn] = StubV2Peer(vendor, cn, retrieval)

    reviewer = _cli_reviewer if (interactive and sys.stdin.isatty()) else None
    trace = TraceWriter.new("ideation")
    try:
        rec, candidates = asyncio.run(
            run_ideation(
                topic,
                peers,
                trace,
                reviewer=reviewer,
                auto_rounds=auto_iterate,
                max_turns=cfg.max_turns,
                max_msgs_per_peer=cfg.max_msgs_per_peer,
                anonymize_on=cfg.anonymize,
                emit=_stream,
            )
        )
    except KeyboardInterrupt:
        raise typer.Abort() from None

    by = {c.id: c for c in candidates}
    winner = by.get(rec.ranked[0]) if rec.ranked else None
    if winner is None:
        ui.console.print("[red]ideation produced no candidate[/red]")
        raise typer.Exit(1)
    _record_ideation(proj, store, pid, winner, trace.run_id)
    ui.console.print(f"\n[green]Stage A complete[/green] · selected: [bold]{winner.title}[/bold]")
    ui.console.print(
        f"  approve & advance → [cyan]council project approve {pid}[/cyan]   ·   "
        f"status → council project status {pid}"
    )


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


def _reviewer_seats(cfg, lead_vendor):
    """The other two seats are reviewers; single-seat configs review with the lead seat."""
    others = [(v, m) for v, m in cfg.seats.items() if v != lead_vendor]
    return others or [(lead_vendor, cfg.seats[lead_vendor])]


def _run_stage_b(handoff, allow_local: bool, profile: str = "balanced", out_dir=None):
    """Real Stage B council loop: author implements → sandbox runs → 2 peers review (typed
    findings + optional probe) → revise, until feasible+approved or the cap. Falls back to the
    stub if no isolated sandbox is available. Writes code/result/log/reviews under
    <out_dir>/experiment/. Returns (summary, artifacts)."""
    from research_council.agents.agent_peer import agent_model_name
    from research_council.agents.code_reviewer import CodeReviewer
    from research_council.agents.coder import Coder
    from research_council.debate.caps import stage_b_caps, total_spend
    from research_council.debate.experimentation import (
        load_prior_experiments,
        run_experiments,
        write_experiments,
    )
    from research_council.lifecycle import run_stage_stub
    from research_council.store.models import Candidate, ResearchQuestion
    from research_council.verify.sandbox import build_sandbox

    sandbox, warn = build_sandbox("docker", allow_local=allow_local)
    if sandbox is None:
        ui.console.print(f"[yellow]{warn} — falling back to stub.[/yellow]")
        return run_stage_stub("experimentation", handoff)
    if warn:
        ui.console.print(f"[yellow]{warn}[/yellow]")

    cfg = load_config("ideation")
    author = "anthropic" if "anthropic" in cfg.seats else next(iter(cfg.seats))
    coder = Coder(agent_model_name(author, cfg.seats[author]), price_model=cfg.seats[author])
    reviewers = [
        CodeReviewer(agent_model_name(v, m), vendor=v, price_model=m)
        for v, m in _reviewer_seats(cfg, author)
    ]
    caps = stage_b_caps(profile)

    try:
        rqs = Candidate.model_validate(handoff.idea).numbered_rqs()
    except Exception:
        rqs = [
            ResearchQuestion(
                id="rq1",
                question=handoff.idea.get("hypothesis", "") or handoff.idea.get("title", ""),
                plan=handoff.experiment_plan,
                metrics=handoff.idea.get("dataset_metrics", ""),
            )
        ]

    def _emit(ph, k, pl):
        if k == "rq_start":
            extra = f"[bold]{pl['rq_id']}[/bold] {pl['question'][:60]}"
        elif k == "rq_done":
            extra = f"{pl['rq_id']} · feasible={pl['feasible']} approved={pl['approved']} · {pl.get('metric') or '—'}"
        elif k == "sandbox_run":
            extra = f"attempt {pl['attempt']} · ran={pl['ok']} feasible={pl.get('feasible')} [{pl['backend']}]"
        elif k == "code_review":
            nb = sum(
                1
                for f in pl["findings"]
                if f["kind"] in ("correctness", "soundness") and f["severity"] == "high"
            )
            extra = f"{pl['vendor']} · {'approve' if pl['approve'] else 'changes'} · {len(pl['findings'])} findings ({nb} blocking)"
        else:
            extra = ""
        ui.stream_line(0, ph, k, None, extra)

    prior = load_prior_experiments(out_dir) if out_dir is not None else {}
    if prior:
        ui.console.print(
            f"  [dim]continuing from {len(prior)} prior experiment(s) — improving, not rebuilding[/dim]"
        )
    ui.console.print(
        f"  [dim]Stage B · council running {len(rqs)} experiment(s) in the {sandbox.name} "
        f"sandbox ({profile}: ≤{caps.max_iters} iters/RQ, K={caps.k}, ${caps.usd_budget}/RQ)…[/dim]"
    )
    rq_results = asyncio.run(
        run_experiments(
            handoff.idea, rqs, coder, reviewers, sandbox, caps=caps, prior=prior, emit=_emit
        )
    )
    cost = total_spend(coder, *reviewers)
    feasible = sum(1 for rr in rq_results if rr.result.feasible)
    approved = sum(1 for rr in rq_results if rr.result.approved)
    n = len(rq_results)
    tone = "green" if approved == n else "yellow"
    ui.console.print(
        f"  [{tone}]{approved}/{n} RQ(s) approved[/{tone}] · {feasible}/{n} feasible · ${cost:.4f}"
    )
    artifacts = {
        "idea": handoff.idea,
        "experiment_plan": handoff.experiment_plan,
        "rqs": [rr.model_dump() for rr in rq_results],
        "feasible_count": feasible,
        "approved_count": approved,
        "rq_count": n,
        "usd": cost,
    }
    if out_dir is not None:
        exp_dir = write_experiments(rq_results, out_dir)
        artifacts["experiment_dir"] = str(exp_dir)
        artifacts["results_csv"] = str(exp_dir / "results.csv")
        ui.console.print(f"  → {exp_dir}  ·  results.csv")
    summary = f"{approved}/{n} RQ(s) approved · {feasible}/{n} feasible"
    return summary, artifacts


def _venue_choice(handoff, venue_flag, *, live: bool):
    """Resolve the Stage-C target venue: --venue wins; else (interactive) the council
    recommends a best-fit venue and the human confirms/overrides; non-interactive falls back
    to the handoff constraint or generic. Returns a VenueChoice."""
    from research_council.debate.writing import list_venues
    from research_council.store.models import VenueChoice

    venues = list_venues()
    if venue_flag:
        return VenueChoice(venue=venue_flag if venue_flag in venues else "generic")

    fallback = handoff.constraints.get("venue") or "generic"
    if not sys.stdin.isatty():
        return VenueChoice(venue=fallback if fallback in venues else "generic")

    default, rationale = fallback, ""
    if live:
        from research_council.agents.agent_peer import agent_model_name
        from research_council.agents.writer import VenueRecommender

        cfg = load_config("ideation")
        seat = "anthropic" if "anthropic" in cfg.seats else next(iter(cfg.seats))
        rec = asyncio.run(
            VenueRecommender(
                agent_model_name(seat, cfg.seats[seat]), price_model=cfg.seats[seat]
            ).recommend(handoff.idea, handoff.artifacts or {}, venues)
        )
        default, rationale = rec.venue, rec.rationale
        if rationale:
            ui.console.print(f"  [dim]council suggests [cyan]{default}[/cyan] — {rationale}[/dim]")

    venue = ui.ask_select(
        "Target venue", venues, default=default if default in venues else "generic"
    )
    emphasis = ui.ask_text("Emphasis (optional, what to foreground)")
    db = ui.ask_select("Double-blind?", ["no", "yes"], default="no")
    return VenueChoice(
        venue=venue, emphasis=emphasis, double_blind=(db == "yes"), rationale=rationale
    )


def _run_stage_c(handoff, out_dir, onboarding, profile: str = "balanced"):
    """Real Stage C council writing loop: lead drafts → 2 PC reviewers score vs the venue
    rubric + file change-requests → lead revises targeted sections → re-review, until accept
    or the cap; then a coherence pass + a LaTeX build. Returns (summary, artifacts)."""
    from research_council.agents.agent_peer import agent_model_name
    from research_council.agents.latex_fixer import LatexFixer
    from research_council.agents.writer import PaperReviewer, Writer
    from research_council.debate.caps import stage_c_caps, total_spend
    from research_council.debate.writing import (
        grounded_citations,
        load_prior_paper,
        load_venue,
        run_writing,
    )

    cfg = load_config("ideation")
    v = onboarding.venue
    vname = load_venue(v).get("name", v)
    lead = "anthropic" if "anthropic" in cfg.seats else next(iter(cfg.seats))
    writer = Writer(
        agent_model_name(lead, cfg.seats[lead]), venue=vname, price_model=cfg.seats[lead]
    )
    reviewers = [
        PaperReviewer(agent_model_name(rv, m), venue=vname, vendor=rv, price_model=m)
        for rv, m in _reviewer_seats(cfg, lead)
    ]
    latex_fixer = LatexFixer(agent_model_name(lead, cfg.seats[lead]), price_model=cfg.seats[lead])
    caps = stage_c_caps(profile)
    # if this project already has a paper, continue improving it (don't redraft from scratch)
    prior_draft, build_error = load_prior_paper(out_dir)
    if prior_draft is not None:
        ui.console.print(
            "  [dim]continuing from the existing paper — improving, not rewriting"
            + (" · feeding the prior build error to the council" if build_error else "")
            + "[/dim]"
        )

    # carry the onboarding into the writing constraints the writer/reviewers see
    if onboarding.emphasis:
        handoff.constraints["emphasis"] = onboarding.emphasis
    if onboarding.double_blind:
        handoff.constraints["double_blind"] = "yes"

    def _emit(ph, k, pl):
        if k == "draft":
            extra = f"'{pl.get('title', '')}' · {pl.get('citations', 0)} cites"
        elif k == "reviewer":
            crs = pl.get("change_requests", [])
            top = f" · “{crs[0]['msg'][:54]}”" if crs else ""
            extra = (
                f"  ↳ {pl['vendor']} · mean {pl['mean']:.2f} · {pl.get('verdict', '')} · "
                f"{len(crs)} change-req(s){top}"
            )
        elif k == "review":
            extra = (
                f"round {pl['round']} · mean {pl['mean']:.2f} · {pl['change_requests']} change-reqs"
                + (" · blocking" if pl.get("blocking") else "")
            )
        elif k == "revise":
            extra = f"round {pl['round']} · sections {pl['sections']}"
        elif k == "latex":
            extra = f"{pl['status']}" + (" · pdf" if pl.get("pdf") else "")
        else:
            extra = pl.get("status", "")
        ui.stream_line(0, ph, k, None, extra)

    ui.console.print(
        f"  [dim]Stage C · council writing for {vname} "
        f"({profile}: ≤{caps.max_revisions} revisions, accept ≥{caps.accept}, ${caps.usd_budget})…[/dim]"
    )

    async def _go():
        cites = await grounded_citations(handoff.idea)
        return await run_writing(
            handoff,
            writer,
            reviewers,
            venue=v,
            out_dir=out_dir,
            caps=caps,
            allowed_citations=cites,
            prior_draft=prior_draft,
            build_error=build_error,
            latex_fixer=latex_fixer,
            emit=_emit,
        )

    res = asyncio.run(_go())
    cost = total_spend(writer, *reviewers)
    status = "[green]accepted[/green]" if res.accepted else f"[yellow]{res.stopped_reason}[/yellow]"
    ui.console.print(
        f"  {status} · '{res.title}' · mean {res.review.mean:.2f} · {res.revisions} round(s) · "
        f"latex: {res.latex} · ${cost:.4f}"
    )
    ui.console.print(f"  → {res.paper_path}" + (f"  ·  {res.pdf_path}" if res.pdf_path else ""))
    # plan/25 Gap 4: surface the council's own approval tally so a paper written on unapproved
    # experiments is visible (not silently shipped as a result).
    if res.total_rqs:
        approval_note = f"  council approved {res.approved_rqs}/{res.total_rqs} RQ(s)"
        if res.approved_rqs == 0:
            approval_note += " — [yellow]paper rests on unapproved experiments[/yellow]"
        ui.console.print(f"  [dim]{approval_note}[/dim]")
    summary = (
        f"'{res.title}' · {vname} · {'accepted' if res.accepted else res.stopped_reason} · "
        f"mean {res.review.mean:.2f} · latex {res.latex} · approved {res.approved_rqs}/{res.total_rqs}"
    )
    artifacts = {"idea": handoff.idea, **res.model_dump()}
    return summary, artifacts


def _build_v2_peers(cfg, live: bool):
    """Construct the 3 ideation peers (AgentPeer live / StubV2Peer offline) + retrieval."""
    from research_council.debate.orchestrator_v2 import CODENAMES

    retrieval = build_retrieval(cfg.tools) if live else build_stub_retrieval(cfg.tools)
    peers: dict = {}
    for vendor, model in cfg.seats.items():
        cn = CODENAMES.get(vendor, vendor)
        if live:
            from research_council.agents.agent_peer import AgentPeer, agent_model_name

            peers[cn] = AgentPeer(
                vendor,
                cn,
                agent_model_name(vendor, model),
                retrieval,
                max_iters=cfg.max_iters,
                max_tool_calls=cfg.max_tool_calls,
                price_model=model,
            )
        else:
            from research_council.agents.stub_agent_peer import StubV2Peer

            peers[cn] = StubV2Peer(vendor, cn, retrieval)
    return peers, retrieval


def _facilitator_and_answer(cfg, live: bool, tty: bool):
    """The onboarding facilitator (live only) + a TTY answer callback for its clarifying Qs."""
    facilitator = answer_fn = None
    if live:
        from research_council.agents.agent_peer import agent_model_name
        from research_council.agents.facilitator import Facilitator

        facilitator = Facilitator(
            agent_model_name("anthropic", cfg.facilitator_model), price_model=cfg.facilitator_model
        )
        if tty:

            async def answer_fn(q):  # noqa: E306
                ui.rule("onboarding")
                return ((await ui.ask_text_async(q.question)) or "").strip()

    return facilitator, answer_fn


def _run_ideation_stage(
    topic: str, cfg, *, live: bool, tty: bool, prior_context: str = "", harvest: bool = True
):
    """Run one Stage-A ideation (its own per-round gate handles idea refinement when TTY).
    `prior_context` seeds a 'redo' with the previous proposal so the council IMPROVES it.
    When `harvest` (live only), each round's findings + cited papers are ingested into the
    LLM-wiki at round end, so the NEXT round's research can read them. Returns (winner, run_id)."""
    from research_council.debate.orchestrator_v2 import run_ideation

    peers, retrieval = _build_v2_peers(cfg, live)
    facilitator, answer_fn = _facilitator_and_answer(cfg, live, tty)
    reviewer = _cli_reviewer if tty else None
    trace = TraceWriter.new("ideation")

    on_round_end = None
    if live and harvest:
        from research_council.librarian.ingest import Ingestor

        librarian, _ = _build_librarian()
        on_round_end = _round_harvester(trace, retrieval, topic, librarian, Ingestor(librarian))

    rec, candidates = asyncio.run(
        run_ideation(
            topic,
            peers,
            trace,
            facilitator=facilitator,
            answer_fn=answer_fn,
            reviewer=reviewer,
            weights=cfg.weights,
            auto_rounds=1,
            max_turns=cfg.max_turns,
            max_msgs_per_peer=cfg.max_msgs_per_peer,
            prior_context=prior_context,
            on_round_end=on_round_end,
            anonymize_on=cfg.anonymize,
            emit=_stream,
        )
    )
    by = {c.id: c for c in candidates}
    winner = by.get(rec.ranked[0]) if rec.ranked else None
    return winner, trace.run_id


def _ideation_redo_context(proj, *, tty: bool) -> str:
    """Build the 'improve this proposal' seed for a redo of ideation (prior proposal + a note)."""
    from research_council.store.models import Candidate

    idea = proj.stages["ideation"].artifacts.get("idea") or {}
    try:
        prop = Candidate.model_validate(idea).as_proposal_md()
    except Exception:
        prop = idea.get("title", "")
    note = (ui.ask_text("What should this round improve? (optional)") or "").strip() if tty else ""
    ctx = (
        "A previous ideation round produced the proposal below. IMPROVE on it — sharpen its "
        "novelty / soundness / feasibility, or take a stronger angle; do NOT merely restate it.\n\n"
        f"{prop}"
    )
    if note:
        ctx += f"\n\nHuman guidance for this round: {note}"
    return ctx


def _write_proposal(store, pid: str, winner) -> Path:
    """Persist the Stage-A research proposal as projects/<id>/proposal.md."""
    d = store.root / pid
    d.mkdir(parents=True, exist_ok=True)
    path = d / "proposal.md"
    path.write_text(winner.as_proposal_md(), encoding="utf-8")
    return path


def _record_ideation(proj, store, pid: str, winner, run_id):
    """Record the Stage-A proposal artifact (full proposal dict + proposal.md path)."""
    from research_council.lifecycle import record_result

    pp = _write_proposal(store, pid, winner)
    record_result(
        proj,
        "ideation",
        run_id=run_id,
        summary=winner.title,
        artifacts={
            "idea": winner.model_dump(),
            "experiment_plan": winner.experiment_plan,
            "proposal_path": str(pp),
        },
    )
    store.save(proj)
    ui.console.print(f"  proposal → [cyan]{pp}[/cyan]")
    return pp


def _gate(question: str, go_label: str, redo_label: str, *, tty: bool) -> str:
    """Conversational gate. Non-TTY auto-proceeds ('go') so the pipeline is scriptable."""
    if not tty:
        return "go"
    choice = ui.ask_select(question, [go_label, redo_label, "✗ stop here"])
    if choice == go_label:
        return "go"
    return "redo" if choice == redo_label else "stop"


def _advance_into(proj, nxt, handoff, store, *, live: bool, profile: str, venue):
    """Run the engine for `nxt` (live) or the stub (offline). Returns (summary, artifacts)."""
    from research_council.lifecycle import run_stage_stub

    if not live:
        return run_stage_stub(nxt, handoff)
    if nxt == "experimentation":
        return _run_stage_b(handoff, False, profile, out_dir=store.root / proj.id)
    onboarding = _venue_choice(handoff, venue, live=live)
    return _run_stage_c(handoff, store.root / proj.id, onboarding, profile)


@app.command("run")
def run_conductor(
    topic: str = typer.Option(
        None, "--topic", "-t", help="research question (asked if omitted on a TTY)"
    ),
    live: bool = typer.Option(
        False, help="run the real engines (needs keys; Stage B needs Docker)"
    ),
    profile: str = typer.Option(
        None,
        help="cap profile for B/C loops (default RC_PROFILE or balanced): conservative | balanced | thorough",
    ),
    venue: str = typer.Option(
        None, help="Stage C venue (else the council recommends + you confirm)"
    ),
    resume: str = typer.Option(
        None, "--resume", help="resume an existing project's gate loop by id (skip new ideation)"
    ),
    from_stage: str = typer.Option(
        None,
        "--from",
        help="with --resume: rewind to an earlier stage (ideation|experimentation|writing) and "
        "continue from its existing artifacts",
    ),
    harvest: bool = typer.Option(
        True, help="(live) ingest each ideation round's findings into the LLM-wiki at round end"
    ),
):
    """Conversational conductor: onboarding → ideation → experimentation → writing, gated by you.

    One command walks the whole lifecycle; at each stage boundary it tells you the outcome and
    asks whether to go on, redo the stage, or stop — so you answer questions instead of typing a
    command per stage. Resumable later via `council project approve <id>`."""
    import datetime

    from research_council.debate.caps import resolve_profile
    from research_council.lifecycle import (
        ProjectStore,
        approve_and_advance,
        build_handoff,
        is_complete,
        new_project,
        record_result,
        rewind_to,
    )

    profile = resolve_profile(profile)
    cfg = load_config("ideation", profile=profile)  # Stage-A caps scale with the profile too
    tty = sys.stdin.isatty()
    store = ProjectStore()

    if from_stage and not resume:
        ui.info("--from requires --resume <id> (it rewinds an existing project).")
        raise typer.Exit(1)

    if resume:
        # re-enter the gate loop on an existing project — no new ideation
        if not store.exists(resume):
            ui.info(f"no project {resume!r} to resume.")
            raise typer.Exit(1)
        proj, pid = store.load(resume), resume
        if from_stage:  # rewind to an earlier stage; its artifacts become the continue-point
            try:
                rewind_to(proj, from_stage)
            except ValueError as e:
                ui.info(str(e))
                raise typer.Exit(1) from None
            store.save(proj)
        ui.banner(
            f"Council · {pid} (resume{' · from ' + from_stage if from_stage else ''})",
            f"{proj.topic}  ·  {'live' if live else 'offline'}  ·  profile {profile}",
        )
        if is_complete(proj):
            ui.console.print("[bold green]project already complete 🎉[/bold green]")
            return
        if proj.stages[proj.current].status != "awaiting_approval":
            ui.info(
                f"project is at {proj.current}/{proj.stages[proj.current].status} — "
                f"resume continues from a gate. Try `council project approve {pid}`."
            )
            raise typer.Exit(1)
    else:
        if not topic:
            topic = (ui.ask_text("What's your research question?") if tty else "").strip()
        if not topic:
            ui.info("a research question is required (pass --topic on a non-TTY).")
            raise typer.Exit(1)
        if tty:
            _interactive_setup(cfg, live)  # seat models (live) + retrieval tools, then confirm

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        pid = f"{_proj_slug(topic)}-{stamp[-6:]}"
        proj = new_project(topic, pid, created=stamp)
        store.save(proj)
        ui.banner(
            f"Council · {pid}", f"{topic}  ·  {'live' if live else 'offline'}  ·  profile {profile}"
        )

        winner, run_id = _run_ideation_stage(topic, cfg, live=live, tty=tty, harvest=harvest)
        if winner is None:
            ui.console.print("[red]ideation produced no candidate[/red]")
            raise typer.Exit(1)
        _record_ideation(proj, store, pid, winner, run_id)

    _PROMPTS = {
        "ideation": ("Run experiments on this idea?", "▶ run experiments", "↻ redo ideation"),
        "experimentation": ("Write up the paper?", "▶ write the paper", "↻ re-run experiment"),
        "writing": ("Finish the project?", "✓ finish", "↻ re-run writing"),
    }
    _PRIOR = {"experimentation": "ideation", "writing": "experimentation"}

    try:
        while True:
            cur = proj.current
            ui.rule(f"gate · {cur}")
            ui.console.print(f"  [bold]{proj.stages[cur].summary}[/bold]")
            q, go_label, redo_label = _PROMPTS[cur]
            action = _gate(q, go_label, redo_label, tty=tty)

            if action == "stop":
                ui.console.print(
                    f"[yellow]paused at {cur}[/yellow] · resume → "
                    f"[cyan]council run --resume {pid}[/cyan]"
                )
                break

            if action == "redo":
                if cur == "ideation":
                    prior_ctx = _ideation_redo_context(proj, tty=tty)
                    w, rid = _run_ideation_stage(
                        proj.topic,
                        cfg,
                        live=live,
                        tty=tty,
                        prior_context=prior_ctx,
                        harvest=harvest,
                    )
                    if w is not None:
                        _record_ideation(proj, store, pid, w, rid)
                else:
                    handoff = build_handoff(proj, _PRIOR[cur])
                    # reuse the venue already chosen for this project — don't re-ask on a redo
                    redo_venue = venue or proj.stages[cur].artifacts.get("venue")
                    summary, artifacts = _advance_into(
                        proj, cur, handoff, store, live=live, profile=profile, venue=redo_venue
                    )
                    record_result(proj, cur, summary=summary, artifacts=artifacts)
                store.save(proj)
                continue

            # go → approve current stage and advance
            proj, handoff = approve_and_advance(proj)
            if handoff is None:
                store.save(proj)
                ui.console.print("[bold green]project complete 🎉[/bold green]")
                ui.console.print(f"  artifacts under [cyan]{store.root / pid}[/cyan]")
                break
            nxt = proj.current
            summary, artifacts = _advance_into(
                proj, nxt, handoff, store, live=live, profile=profile, venue=venue
            )
            record_result(proj, nxt, summary=summary, artifacts=artifacts)
            store.save(proj)
    except KeyboardInterrupt:
        ui.console.print(f"\n[yellow]paused[/yellow] · resume → council run --resume {pid}")
        raise typer.Abort() from None


@project_app.command("approve")
def project_approve(
    pid: str = typer.Argument(..., help="project id"),
    live: bool = typer.Option(
        False, help="run the real next-stage engine (needs keys; B needs a sandbox)"
    ),
    allow_local_sandbox: bool = typer.Option(
        False,
        "--allow-local-sandbox",
        help="if Docker is absent, run generated code UNISOLATED (unsafe)",
    ),
    venue: str = typer.Option(
        None, help="Stage C target venue (icse/fse/ase/neurips/emnlp/iclr/generic)"
    ),
    profile: str = typer.Option(
        None,
        help="cap profile for B/C loops (default RC_PROFILE or balanced): conservative | balanced | thorough",
    ),
):
    """Approve the current stage and advance (running the next stage's engine)."""
    from research_council.debate.caps import resolve_profile
    from research_council.lifecycle import (
        ProjectStore,
        approve_and_advance,
        record_result,
        run_stage_stub,
    )

    profile = resolve_profile(profile)
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
    if handoff is not None:  # advanced to a next stage → run it
        nxt = p.current
        if nxt == "experimentation" and live:
            summary, artifacts = _run_stage_b(
                handoff, allow_local_sandbox, profile, out_dir=store.root / pid
            )  # real Stage B
        elif nxt == "writing" and live:
            onboarding = _venue_choice(handoff, venue, live=live)
            summary, artifacts = _run_stage_c(
                handoff, store.root / pid, onboarding, profile
            )  # real Stage C
        else:
            summary, artifacts = run_stage_stub(nxt, handoff)  # offline → stub
        record_result(p, nxt, summary=summary, artifacts=artifacts)
        ui.console.print(f"[green]approved {cur}[/green] → ▶ [cyan]{nxt}[/cyan]\n  {summary}")
        ui.console.print(f"  next: [cyan]council project approve {pid}[/cyan]")
    else:
        ui.console.print(f"[green]approved {cur}[/green] · [bold]project complete 🎉[/bold]")
    store.save(p)


if __name__ == "__main__":
    app()
