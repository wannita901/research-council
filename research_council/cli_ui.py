"""Styled interactive CLI helpers — rich (panels/rules/tables) + questionary prompts.

Every user prompt goes through questionary (prompt_toolkit) so arrow keys and line
editing work everywhere — plain input() leaked escape codes (^[[C/^[[D) on ←/→.

Two prompt flavors:
  • sync `ask_*`        — for setup, which runs BEFORE asyncio.run (no running loop).
  • async `ask_*_async` — for onboarding/review callbacks, which run INSIDE the orchestrator's
    event loop, where questionary's .ask() would nest a second loop and fail.
"""

from __future__ import annotations

from contextlib import contextmanager

import questionary
from questionary import Separator, Style
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()
AXES = ("novelty", "soundness", "feasibility", "clarity")

QSTYLE = Style([
    ("qmark", "fg:#6ea8fe bold"),
    ("question", "bold"),
    ("pointer", "fg:#6ea8fe bold"),
    ("highlighted", "fg:#6ea8fe bold"),
    ("selected", "fg:#4ade80 bold"),
    ("answer", "fg:#6ea8fe bold"),
    ("instruction", "fg:#8b94a3 italic"),
    ("separator", "fg:#48505e"),
])
_POINTER = "❯"

PHASE_COLOR = {
    "onboarding": "magenta", "research": "cyan", "propose": "blue",
    "deliberate": "yellow", "judge": "green", "review": "white", "run": "bright_black",
}


def sep() -> Separator:
    return Separator()


def banner(title: str, subtitle: str = "") -> None:
    body = f"[bold]{title}[/bold]" + (f"\n[dim]{escape(subtitle)}[/dim]" if subtitle else "")
    console.print(Panel.fit(body, border_style="cyan", box=box.ROUNDED))


def rule(text: str) -> None:
    console.rule(f"[bold cyan]{escape(text)}[/bold cyan]", align="left")


def info(text: str) -> None:
    console.print(text)


# ---- sync prompts (setup, before the event loop starts) ----
def _select(message, choices, default=None, instruction="↑/↓ · enter"):
    return questionary.select(message, choices=choices, default=default, style=QSTYLE,
                              instruction=instruction, qmark="?", pointer=_POINTER)


def ask_select(message, choices, default=None, instruction="↑/↓ · enter"):
    return _select(message, choices, default, instruction).ask()


def ask_checkbox(message, choices, instruction="space toggles · enter continues"):
    return questionary.checkbox(message, choices=choices, style=QSTYLE,
                                instruction=instruction, qmark="?", pointer=_POINTER).ask()


def ask_text(message, default=""):
    return questionary.text(message, default=default, style=QSTYLE, qmark="?").ask()


# ---- async prompts (onboarding / review, inside the orchestrator loop) ----
async def ask_text_async(message, default=""):
    return await questionary.text(message, default=default, style=QSTYLE, qmark="?").ask_async()


async def ask_select_async(message, choices, default=None, instruction="↑/↓ · enter"):
    return await _select(message, choices, default, instruction).ask_async()


def setup_summary(seats: dict, tools: list, *, live: bool,
                  facilitator: str | None = None, rounds: int | None = None) -> None:
    t = Table(box=box.SIMPLE_HEAVY, show_header=False, pad_edge=False)
    t.add_column(style="dim")
    t.add_column(style="bold")
    if live:
        for v, m in seats.items():
            t.add_row(f"seat · {v}", m)
    t.add_row("tools", ", ".join(tools) or "(none)")
    if facilitator:
        t.add_row("facilitator", facilitator)
    if rounds is not None:
        t.add_row("rounds", str(rounds))
    t.add_row("mode", "[green]✓ live[/green]" if live else "[yellow]offline[/yellow]")
    console.print(Panel(t, title="[bold]ready[/bold]", border_style="green", box=box.ROUNDED))


def stream_line(round_no: int, phase: str, kind: str, author: str | None, extra: str) -> None:
    color = PHASE_COLOR.get(phase, "white")
    who = f" [dim]{escape(author)}[/dim]" if author else ""
    console.print(f"  [dim]r{round_no}[/dim] [{color}]{phase:<10}[/{color}] "
                  f"[bold]{kind}[/bold]{who}  {escape(extra)}")


def ranking_table(ranked, composites, titles, breakdown=None, *, title) -> None:
    t = Table(title=title, box=box.SIMPLE_HEAVY, title_style="bold", title_justify="left")
    t.add_column("#", justify="right", style="dim")
    t.add_column("score", justify="right", style="bold green")
    t.add_column("codename", style="cyan")
    if breakdown:
        for a in AXES:
            t.add_column(a[:4], justify="right", style="dim")
    t.add_column("idea", overflow="fold")
    for i, cid in enumerate(ranked, 1):
        row = [str(i), f"{composites.get(cid, 0):.3f}", cid]
        if breakdown:
            b = breakdown.get(cid, {})
            row += [f"{b.get(a, 0):.2f}" for a in AXES]
        row.append(titles.get(cid, ""))
        t.add_row(*row)
    console.print(t)


def usage_table(rows, *, cache=None, total=0.0, title="Usage (approx · edit PRICES in providers/sdk.py)") -> None:
    """rows: list of (name, cost_usd, in_tok, out_tok, reqs, tool_calls_or_None)."""
    t = Table(title=title, box=box.SIMPLE_HEAVY, title_style="bold", title_justify="left")
    t.add_column("agent", style="cyan")
    t.add_column("$", justify="right", style="bold green")
    t.add_column("in", justify="right", style="dim")
    t.add_column("out", justify="right", style="dim")
    t.add_column("reqs", justify="right")
    t.add_column("tools", justify="right")
    for name, cost, it, ot, reqs, tools in rows:
        t.add_row(name, f"{cost:.4f}", f"{it:,}", f"{ot:,}", str(reqs), "" if tools is None else str(tools))
    console.print(t)
    if cache is not None:
        h, m = cache
        rate = 100 * h / (h + m) if (h + m) else 0
        console.print(f"  [dim]retrieval cache: {h} hits / {m} misses ({rate:.0f}% saved)[/dim]")
    console.print(f"  [bold green]TOTAL  ${total:.4f}[/bold green]")


@contextmanager
def spinner(text: str):
    with console.status(f"[cyan]{escape(text)}", spinner="dots"):
        yield


@contextmanager
def progress(description: str):
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  BarColumn(), MofNCompleteColumn(), console=console, transient=True) as p:
        yield p
