# research-council

Heterogeneous **cross-vendor** multi-agent debate for **AI4SE research**, grounded in **executable verification**. Three peer agents (one per vendor: OpenAI · Claude · Gemini) research a topic independently, propose research ideas + minimal experiment plans, cross-critique, rebut, verify feasibility, and vote — you confirm.

Full design lives in [`plan/`](plan/1_sota-gap-analysis.html) (open in a browser). This repo is the implementation.

## Status — v2 agentic ideation (offline-runnable)

The agentic **ideation** loop — intake → research → propose → deliberate → judge → human gate, with per-round memory — runs **fully offline** via deterministic stub peers (no API keys) and streams a JSONL trace. Cross-vendor live providers (OpenAI · Claude · Gemini), real retrieval (OpenAlex / arXiv / Semantic Scholar / GitHub), and a FastAPI backend are built. Experimentation & writing stages are next. Full design in [`plan/`](plan/1_sota-gap-analysis.html).

## Quickstart (mise)

[mise](https://mise.jdx.dev/) manages the toolchain, the `.venv`, env vars, and tasks — no `.env`.

```bash
cp mise.local.toml.example mise.local.toml   # fill in API keys (only needed for `live`)
mise trust && mise install                   # install python + auto-create .venv
mise run deps                                # install the package + deps

mise run test
mise run ideate -- --topic "Do LLM code-review agents catch security bugs better than SAST?"  # v2, offline
mise run check                               # ping providers (needs keys)
# live (spends real tokens; keys from mise.local.toml):
mise run ideate -- --topic "..." --live

ls runs/                                     # runs/<id>/trace.jsonl
```

The CLI binary is **`council`** (e.g. `council ideate`, `council debate`, `council check`); `mise run <task>` just invokes it inside the managed env (toolchain + `.venv` + keys). Secrets come from `mise.local.toml` (gitignored), so always run via `mise run`/`mise exec` (or `mise activate`) — that's why bare `council check` outside mise reports "key not set".

## Layout

```
research_council/   providers/ retrieval/ librarian/ agents/ debate/ verify/ obs/ store/ config/ cli.py
knowledge/          raw/external/ raw/internal/ wiki/ CLAUDE.md    # the LLM-wiki data
runs/               per-debate JSONL traces (gitignored)
plan/               design docs (HTML)
```

Canonical references: repo layout → `plan/2`; data contracts → `plan/6`; decisions → `plan/3`; eval → `plan/5`; knowledge/librarian → `plan/8`,`plan/9`.
