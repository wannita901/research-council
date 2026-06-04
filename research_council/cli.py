"""`rc` CLI. Offline (stub) debate by default; --live uses real providers."""

from __future__ import annotations

import asyncio

import typer

from research_council.agents.stub_peer import StubPeer
from research_council.config import load_config, parse_seats, parse_tools
from research_council.debate.orchestrator import run_debate
from research_council.retrieval.registry import build_retrieval
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import RunConfig
from research_council.verify.mock import MockVerifier

app = typer.Typer(add_completion=False, help="research-council: cross-vendor AI4SE debate")


def _build_peers(cfg: RunConfig, live: bool):
    if not live:
        return [StubPeer(v) for v in cfg.seats]
    from research_council.agents.llm_peer import LLMPeer
    from research_council.providers.sdk import build_provider
    return [LLMPeer(v, build_provider(v, m)) for v, m in cfg.seats.items()]


@app.command()
def debate(
    topic: str = typer.Option(..., "--topic", "-t", help="research question / topic"),
    stage: str = typer.Option("ideation", help="lifecycle stage (config name)"),
    seats: str = typer.Option(None, help="vendor=model,... override"),
    tools: str = typer.Option(None, help="comma list, e.g. wiki,openalex,arxiv"),
    rounds: int = typer.Option(None, help="max debate rounds"),
    anonymize: bool = typer.Option(None, help="anonymize authorship (bias control)"),
    live: bool = typer.Option(False, help="use real providers (needs keys + SDKs)"),
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

    peers = _build_peers(cfg, live)
    retrieval = build_retrieval(cfg.tools)
    verifier = MockVerifier()
    trace = TraceWriter.new(cfg.stage)

    typer.echo(f"seats  {cfg.seats}")
    typer.echo(f"tools  {cfg.tools}   anonymize={cfg.anonymize}   rounds={cfg.n_rounds}")

    rec, candidates = asyncio.run(run_debate(cfg, topic, peers, retrieval, verifier, trace))

    titles = {c.id: c.title for c in candidates}
    typer.echo("\nRanking (verifier-weighted panel vote):")
    for i, cid in enumerate(rec.ranked, 1):
        typer.echo(f"  {i}. {rec.composites[cid]:.3f}  {titles.get(cid, cid)}")
    typer.echo(f"\ntrace: {trace.path}")


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
