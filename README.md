# research-council

Heterogeneous **cross-vendor** multi-agent debate for **AI4SE research**, grounded in **executable verification**. Three peer agents (one per vendor: OpenAI · Claude · Gemini) research a topic independently, propose research ideas + minimal experiment plans, cross-critique, rebut, verify feasibility, and vote — you confirm.

Full design lives in [`plan/`](plan/1_sota-gap-analysis.html) (open in a browser). This repo is the implementation.

## Status — Increment 1 (offline spine)

The 7-phase debate loop runs **fully offline** via deterministic *stub peers* — no API keys required — and writes a JSONL trace. Real provider SDKs, live retrieval, and the sandbox verifier are wired behind interfaces but stubbed (see TODOs).

## Quickstart (mise)

[mise](https://mise.jdx.dev/) manages the toolchain, the `.venv`, env vars, and tasks — no `.env`.

```bash
cp mise.local.toml.example mise.local.toml   # fill in API keys (only needed for `live`)
mise trust && mise install                   # install python + auto-create .venv
mise run deps                                # install the package + deps

mise run test
mise run debate -- --topic "Do WikiLLM concept retrieve better information than RAG concept?"
# live (spends tokens; keys from mise.local.toml):
mise run live -- --topic "..."

ls runs/                                     # runs/<id>/trace.jsonl
```

Secrets come from `mise.local.toml` (gitignored). Without `mise activate`, run via `mise run`/`mise exec` so the venv + env apply.

## Layout

```
research_council/   providers/ retrieval/ librarian/ agents/ debate/ verify/ obs/ store/ config/ cli.py
knowledge/          raw/external/ raw/internal/ wiki/ CLAUDE.md    # the LLM-wiki data
runs/               per-debate JSONL traces (gitignored)
plan/               design docs (HTML)
```

Canonical references: repo layout → `plan/2`; data contracts → `plan/6`; decisions → `plan/3`; eval → `plan/5`; knowledge/librarian → `plan/8`,`plan/9`.
