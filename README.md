# research-council

Heterogeneous **cross-vendor** multi-agent system for the **AI4SE research lifecycle**, grounded in **executable verification**. Three peer agents (one per vendor: OpenAI · Claude · Gemini) carry a project through three human-gated stages — **ideation → experimentation → writing** — debating a full research proposal, then running & reviewing the experiment in a sandbox, then co-authoring and reviewing the paper. You confirm at every gate.

Full design lives in [`plan/`](plan/1_sota-gap-analysis.html) (open in a browser). This repo is the implementation.

## Status — full A→B→C lifecycle with real engines (offline-runnable)

All three stages have real engines and also run **fully offline** via deterministic stubs (no API keys):

- **A · Ideation** — onboarding → research → propose → deliberate → judge → human gate, with per-round memory. The council argues over a **full research proposal** (problem · motivation · hypothesis · method · step-by-step plan · **research questions** · dataset/metrics · fallback) and every claim must cite a reference or evidence.
- **B · Experimentation** — **one council loop per research question**: a peer implements, two cross-vendor peers review the code *and* the result (with sandbox verification probes); iterate until **feasible (ran + emitted a METRIC) and approved**, in a Docker sandbox. Results aggregate to a `results.csv`.
- **C · Writing** — a council loop: a lead drafts, two PC reviewers score against a **venue rubric** and file change-requests; revise until accepted; then a **LaTeX build-verify-fix** export.

Cross-vendor live providers, real retrieval (OpenAlex / arXiv / Semantic Scholar / GitHub), the LLM-wiki librarian, and a FastAPI backend are all built. Designs in [`plan/`](plan/1_sota-gap-analysis.html) (newest: [18](plan/18_stage-bc-loops.html) loops · [19](plan/19_conversational-conductor.html) conductor · [20](plan/20_proposal-and-evidence.html) proposal + evidence).

## Setup (once)

[mise](https://mise.jdx.dev/) manages the toolchain, the `.venv`, env vars, and tasks — no `.env`.

```bash
cp mise.local.toml.example mise.local.toml   # fill in API keys (only needed for --live)
mise trust && mise install                   # install python 3.14 + auto-create .venv
mise run deps                                # install the package + dev/providers/service deps
mise run test                                # 114 tests, fully offline
```

Put `council` on your PATH so you don't prefix every command (otherwise use `mise exec -- council …`):

```bash
eval "$(mise activate zsh)"                  # add to ~/.zshrc; then `council …` works in this repo
```

## Run it — the whole lifecycle in one conversation

`council run` is the conductor: it walks onboarding → ideation → experimentation → writing and **pauses at each stage to ask you** (go / redo / stop) — you answer questions instead of typing a command per stage.

```bash
council run                                  # offline: stub council + stub B/C, walks A→B→C end to end
council run --live --profile balanced        # real engines (see prerequisites below)
```

At each gate it shows the outcome and asks what to do next; `stop` saves and you can resume later (below).

### Prerequisites for `--live`

1. **API keys** in `mise.local.toml` — verify with `council check` (pings each vendor, ~cents).
2. **Docker** running — Stage B runs generated code in an isolated sandbox (`--network none`). Without it, pass `--allow-local-sandbox` to run **unisolated** (unsafe; opt-in only).
3. **tectonic** or **latexmk** (optional) — Stage C compiles `paper.pdf`; without a TeX engine it still emits `paper.tex`.

`--profile conservative|balanced|thorough` bounds the B/C loops (iterations · approval bar · USD budget). `--venue icse|fse|ase|neurips|emnlp|iclr|generic` sets the paper's target (omit and the council recommends one for you to confirm).

## Run it — stage by stage (scriptable / resumable)

The conductor sits on these; use them directly to script, resume, or re-run a single stage:

```bash
council project new --topic "Do LLM code-review agents catch security bugs better than SAST?" --live
council project status <id>                          # where it is + the selected proposal
council project approve <id> --live --profile balanced          # → runs Stage B
council project approve <id> --live --venue icse --profile balanced   # → runs Stage C
council project approve <id>                         # → marks the project complete
```

Drop `--live` anywhere to walk the lifecycle with stubs (no keys/Docker needed).

### Outputs

```
projects/<id>/project.json         # lifecycle state + every stage's artifacts
projects/<id>/proposal.md          # Stage A — research proposal (incl. research questions)
projects/<id>/experiment/          # Stage B — one council loop per RQ
  ├── results.csv                  #   aggregated: rq_id, question, metric, value, feasible, approved, …
  └── rq1/, rq2/, …                #   per RQ: experiment.py · log.txt · reviews.md · question.md
projects/<id>/paper/paper.md       # Stage C — paper (+ sections/, review.md, assets/, paper.tex/pdf)
runs/<id>/trace.jsonl              # full event trace for every run
```

So everything for a project — proposal, code, results, reviews, and paper — lives under `projects/<id>/`.

The CLI binary is **`council`** (`council run`, `council ideate`, `council debate`, `council check`); `mise run <task>` invokes it inside the managed env. Secrets come from `mise.local.toml` (gitignored), so run via `mise run`/`mise exec` or after `mise activate` — bare `council check` outside mise reports "key not set".

## Layout

```
research_council/   providers/ retrieval/ librarian/ agents/ debate/ verify/ obs/ store/ config/ cli.py
  config/venues/     icse · fse · ase · neurips · emnlp · iclr · generic (rubrics)
knowledge/          raw/external/ raw/internal/ wiki/ CLAUDE.md    # the LLM-wiki data
projects/           per-project lifecycle state + artifacts (proposal.md, paper/) (gitignored)
runs/               per-run JSONL traces (gitignored)
plan/               design docs (HTML; open in a browser)
```

Canonical references: lifecycle → `plan/13`; Stage B/C loops → `plan/18`; conductor → `plan/19`; proposal + evidence rule → `plan/20`; repo layout → `plan/2`; data contracts → `plan/6`; knowledge/librarian → `plan/8`,`plan/9`.
