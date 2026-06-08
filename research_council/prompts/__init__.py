"""Central prompt store — one `.md` file per prompt, grouped by agent in subfolders.

    research_council/prompts/
      peer/        research · propose · judge · deliberate · finalize   (v2 AgentPeer)
      facilitator/ intake
      librarian/   router · audit
      peer_v1/     research · propose · critique · score                (legacy llm_peer)

    from research_council import prompts
    prompts.load("peer/research", codename="Aiden")   # → str, with {placeholders} filled
    prompts.load("librarian/router")                    # → str, verbatim (no placeholders)

Placeholders use Python str.format syntax (e.g. ``{codename}``). Prompts that contain
literal braces (the v1 JSON examples) are loaded WITHOUT kwargs so they're returned
verbatim. Files are cached per process, so edits take effect on the next run.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def _raw(name: str) -> str:
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise KeyError(f"no prompt {name!r} (expected {path})")
    return path.read_text(encoding="utf-8").strip()


def load(name: str, **kw) -> str:
    """Return prompt `name`; if kwargs are given, fill {placeholders} via str.format."""
    text = _raw(name)
    return text.format(**kw) if kw else text


def names() -> list[str]:
    """All available prompt names as `group/name` (for tooling / sanity checks)."""
    return sorted(p.relative_to(_DIR).with_suffix("").as_posix() for p in _DIR.rglob("*.md"))
