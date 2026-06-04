# Wiki schema — librarian conventions

This directory is the **LLM Wiki** (plan/8, plan/9): an LLM-maintained synthesis over a
curated AI4SE corpus. The librarian (Claude Sonnet) ingests sources and maintains the
typed pages below. **Read side** (peers querying) and **write side** (`rc ingest`/`rc lint`)
are separate; debates read a committed snapshot.

## Layers
- `raw/external/` — real sources (papers, notes). **Only these count as prior art for novelty.**
- `raw/internal/` — system-generated notes. **Never** cited as literature.
- `wiki/` — the maintained markdown (this is what peers read).

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
- Pages are markdown + YAML frontmatter: `type`, `title`, `papers:[citekeys]`, `related:[[type:slug]]`, `updated`.
- Slugs are kebab-case; cross-links use `[[type:slug]]`.
- `index.md` is the catalog; `log.md` is the append-only ingest/lint history.
- Maintainer model + merge gate live in `research_council/config/wiki.yaml`.
