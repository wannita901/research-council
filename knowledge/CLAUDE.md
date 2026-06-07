# Wiki schema — librarian conventions

This directory is the **LLM Wiki** (plan/8, plan/9, revisited in plan/16): an LLM-maintained
synthesis over a curated AI4SE corpus. The librarian (Claude Sonnet) ingests sources and
maintains the typed pages below. **Read side** (peers querying via the `search` tool) and
**write side** (`council ingest` / `council lint` / post-run harvest) are separate. Ingest
**auto-merges** into `wiki/` and appends `log.md`; it does **not** git-commit — review with
`git diff` and commit on your own cadence. Writes are offline / post-run, never mid-debate.

## Layers
- `raw/external/` — real sources only (papers, notes). **Immutable**: the librarian reads them
  and saves harvested source copies here, but never rewrites them.
- `wiki/` — the LLM-owned synthesis (this is what peers read). The librarian owns it entirely.

**Contamination guard (the `origin` flag, not the folder):** every `wiki/` page carries
`origin: external | internal`.
- `origin: external` — derived from real sources → **counts as prior art for novelty**.
- `origin: internal` — the council's own synthesis, filed back from a run (Karpathy: "good
  answers are filed back into the wiki as new pages") → **never** counted as prior art.
A page touched by any external source escalates to `origin: external`. Gap-finding reads
everything; novelty-scoring filters to `origin: external`. (There is no `raw/internal/`.)

## Page taxonomy (route content here)
| Folder | Holds — the question it answers |
| --- | --- |
| `papers/` | one note per source; the anchor that links to the typed pages below |
| `tasks/` | WHAT problem — the SE goal (program repair, flaky-test detection) |
| `motivations/` | WHY — why the problem/design matters; assumptions |
| `concepts/` | the IDEA — SE philosophy, framing, theory |
| `approaches/` | HOW (technical) — the technique a paper builds |
| `methods/` | HOW (validated) — study design, baselines, metrics, stats |
| `benchmarks/` | datasets/benchmarks themselves |
| `findings/` | results/claims, cross-paper, each with evidence refs |
| `gaps/` | open problems → feeds ideation |

**Routing disambiguation:** approaches = built · methods = validated · concepts = idea · motivations = why.

## Conventions
- Pages are markdown + YAML frontmatter: `type`, `title`, `origin` (`external`|`internal`),
  `papers:[citekeys]`, `related:[[type:slug]]`, `updated`.
- Slugs are kebab-case; cross-links use `[[type:slug]]`. The `papers/` anchor links to every
  typed page produced from its source.
- `index.md` is the catalog (regenerated from the tree on every ingest); `log.md` is the
  append-only ingest/lint history.
- Maintainer model lives in `research_council/config/wiki.yaml`; merge = auto-merge + `log.md`
  audit (no git commit). `council lint` checks broken links, orphans, index drift, empty pages.
