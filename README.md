# research-council

Heterogeneous **cross-vendor** multi-agent debate for **AI4SE research**, grounded in **executable verification**. Three peer agents (one per vendor: OpenAI · Claude · Gemini) research a topic independently, propose research ideas + minimal experiment plans, cross-critique, rebut, verify feasibility, and vote — you confirm.

Full design lives in [`plan/`](plan/1_sota-gap-analysis.html) (open in a browser). This repo is the implementation.

## Status — Increment 1 (offline spine)

The 7-phase debate loop runs **fully offline** via deterministic *stub peers* — no API keys required — and writes a JSONL trace. Real provider SDKs, live retrieval, and the sandbox verifier are wired behind interfaces but stubbed (see TODOs).

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Offline run (stub peers, mock verifier) — no keys needed:
rc debate --topic "Do LLM code-review agents catch security bugs better than SAST?"

# Inspect the trace:
ls runs/                       # runs/<id>/trace.jsonl
```

`--live` switches to real providers (needs keys in `.env` + `pip install -e ".[providers]"`).

## Layout

```
research_council/   providers/ retrieval/ librarian/ agents/ debate/ verify/ obs/ store/ config/ cli.py
knowledge/          raw/external/ raw/internal/ wiki/ CLAUDE.md    # the LLM-wiki data
runs/               per-debate JSONL traces (gitignored)
plan/               design docs (HTML)
```

Canonical references: repo layout → `plan/2`; data contracts → `plan/6`; decisions → `plan/3`; eval → `plan/5`; knowledge/librarian → `plan/8`,`plan/9`.
