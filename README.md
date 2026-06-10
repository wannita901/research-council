<p align="center">
  <img src="assets/banner.svg" alt="research-council — From question to paper, an autonomous multi-agent research council for AI4SE research" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.14">
  <a href="https://github.com/wannita901/research-council/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/wannita901/research-council/tests.yml?style=flat&logo=github&label=tests" alt="tests"></a>
  <img src="https://img.shields.io/badge/agents-PydanticAI-E92063?style=flat&logo=pydantic&logoColor=white" alt="PydanticAI">
  <img src="https://img.shields.io/badge/CLI-Typer-009485?style=flat&logo=typer&logoColor=white" alt="Typer">
  <img src="https://img.shields.io/badge/sandbox-Docker-2496ED?style=flat&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/toolchain-mise-4B5563?style=flat" alt="mise">
</p>

**research-council** turns a research question into a paper. Three rival frontier models — **OpenAI, Anthropic, and Google** — act as one council and work through the research lifecycle together, with **you approving every step**.

It runs **offline** with stub agents (no API keys — a dry-run of the whole pipeline) or **live** with your own keys for real proposals, experiments, and papers.

## How it works

A *project* moves through three stages. You approve each hand-off; the same three-model council plays a different role in each stage.

```
   research question
          │
          ▼
   ①  IDEATION ───────▶  ②  EXPERIMENTATION ───▶  ③  WRITING ───▶  paper
      debate &              implement & run            draft, peer-review
      propose               (sandboxed, per RQ)        vs venue rubric, revise
          │                       │                          │
       [ you approve ]      [ you approve ]            [ you approve ]
          ▼                       ▼                          ▼
     proposal.md          experiment/results.csv       paper/paper.pdf
```

| Stage | What the council does | You get |
| --- | --- | --- |
| **① Ideation** | Each model researches independently, proposes a full research proposal (problem, hypothesis, method, plan, research questions, metrics), then they debate, critique, and score each other **anonymously**. | `proposal.md` |
| **② Experimentation** | For **each research question**, one model writes the experiment code and two others review the code *and* its result in a Docker sandbox — looping until it actually **runs and is approved**. | `experiment/results.csv` + per-question code, logs, reviews |
| **③ Writing** | A lead model drafts the paper; two act as a program committee, scoring it against the **target venue's rubric** and requesting changes until it's accepted — then it's compiled to PDF. | `paper/paper.pdf` |

Two principles throughout: **every claim must cite a source or show experiment evidence**, and **nothing advances without your approval**.

## Quick Start

A 30-second offline dry-run — no API keys, no Docker:

```bash
# one-time setup
mise trust && mise install && mise run deps   # installs Python 3.14, a venv, and dependencies
eval "$(mise activate bash)"                   # puts the `council` command on your PATH
                                               # (zsh: use `mise activate zsh`; add the line to
                                               #  your ~/.bashrc or ~/.zshrc to make it permanent)

# run the whole lifecycle with stub agents
council run --topic "Do LLM agents catch security bugs better than SAST?"
```

`council run` walks ideation → experimentation → writing and pauses at each stage to ask you: **go / redo / stop**. Offline it uses deterministic stubs, so you see the full flow without spending anything.

## Going live

For real results, add API keys and (for stages B/C) Docker and a LaTeX engine:

```bash
cp mise.local.toml.example mise.local.toml    # then paste your OpenAI / Anthropic / Google keys
council check                                  # verify all three models respond (costs ~cents)

council run --live --profile balanced          # the real thing
```

| Need | Used for | How to check |
| --- | --- | --- |
| **API keys** (3 vendors) | the council's three seats | `council check` |
| **Docker** running | sandboxing experiment code in stage ② (`--network none`) | `docker info` |
| `tectonic` or `latexmk` *(optional)* | compiling `paper.pdf` in stage ③ (otherwise you still get `paper.tex`) | `tectonic --version` |

> Experiments can declare their own pip dependencies — these are installed in a network-enabled prep step, then the code runs with **no network** (real libraries, isolated execution). Optional: `mise run build-image` pre-bakes the common scientific stack (numpy / pandas / scipy / scikit-learn / matplotlib) so those are instant. The experiment also saves real plots, which the paper embeds.

## Commands & flags

**One conversation** (recommended) — the conductor drives the whole lifecycle:

```bash
council run --live --profile balanced
```

**Or stage by stage** — scriptable and resumable. Re-running a stage that already has output **improves it** instead of starting over:

```bash
council project new     --topic "…" --live          # stage ① ideation
council project status  <id>                          # see where it is + the proposal
council project approve <id> --live                   # → runs stage ② experimentation
council project approve <id> --live --venue icse      # → runs stage ③ writing
council project approve <id>                           # → done
```

| Flag | What it does |
| --- | --- |
| `--live` | use real models (omit it for the offline stub dry-run) |
| `--profile` | `conservative` · `balanced` · `thorough` — how hard the council works (see below) |
| `--venue` | `icse` · `fse` · `ase` · `neurips` · `emnlp` · `iclr` · `generic` (omit and the council recommends one) |
| `--allow-local-sandbox` | run experiment code **unsandboxed** when Docker is unavailable (unsafe — opt in only) |

## Configuration

Everything is set in **`mise.toml`** (no `.env`). Choose each seat's model (`RC_OPENAI_MODEL`, `RC_ANTHROPIC_MODEL`, `RC_GEMINI_MODEL`, `RC_FACILITATOR_MODEL`), and pick how hard the council works with **`RC_PROFILE`** — one knob that scales all three stages:

| `RC_PROFILE` | Stage A · Ideation<br><sub>rounds · msgs/peer</sub> | Stage B · Experimentation<br><sub>iters · approvals · $/RQ</sub> | Stage C · Writing<br><sub>revisions · accept · $</sub> | Budget / run\* |
| --- | :---: | :---: | :---: | :---: |
| `conservative` | 2 · 2 | 2 · 1 · $0.60 | 2 · 0.65 · $0.60 | **≈ $4** |
| **`balanced`** *(default)* | 4 · 3 | 3 · 2 · $1.50 | 3 · 0.70 · $1.50 | **≈ $11** |
| `thorough` | 6 · 4 | 5 · 2 · $4.00 | 5 · 0.78 · $4.00 | **≈ $28** |

<sub>\* Rough end-to-end estimate assuming ~3 research questions (stage B cost is per question); real spend varies with the models and topic.</sub>

Need finer control? Every individual cap has an env override — uncomment a `RC_MAX_*` / `RC_STAGEB_*` / `RC_STAGEC_*` line in `mise.toml`. Precedence: **per-field override > profile > default**.

## Outputs

Everything a project produces lives under `projects/<id>/`:

```
projects/<id>/
├── proposal.md         # ①  the research proposal
├── experiment/         # ②  one loop per research question
│   ├── results.csv     #     rq · metric · value · feasible · approved · …
│   └── rq1/ rq2/ …     #     experiment.py · log.txt · reviews.md
└── paper/              # ③  paper.md · sections/ · review.md · paper.tex/pdf
runs/<id>/trace.jsonl   # full event trace of the run
```

## Design & status

The full ideation → experimentation → writing lifecycle has **real engines** end-to-end and also runs offline via stubs. Live providers, real literature retrieval (OpenAlex / arXiv / Semantic Scholar / GitHub), a grounded LLM-wiki, Docker-sandboxed experiments, and a FastAPI backend (stage A) are all built.

Design notes live in [`plan/`](plan/1_sota-gap-analysis.html) (HTML — open in a browser): [lifecycle](plan/13_research-lifecycle.html) · [stage B/C loops](plan/18_stage-bc-loops.html) · [conductor](plan/19_conversational-conductor.html) · [proposal & evidence](plan/20_proposal-and-evidence.html) · [RQ-driven experiments](plan/21_rq-driven-experiments.html) · [deliberation balance](plan/23_deliberation-balance.html).

<sub>GitHub Actions runs the offline test suite on every push and PR. Not yet published to PyPI. Local development via `mise`.</sub>
