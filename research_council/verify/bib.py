"""Citation-to-record resolver + references.bib emitter (plan/25 Gap 2).

The product's headline promise is "every claim must cite a source". Citations ARE filtered
to the LLM-wiki allow-list (writer.py drops keys it didn't grant, so the writer can't invent a
bib key) — but the wiki pages are themselves model-generated prior-art summaries. "grounded"
today means "drawn from our corpus", NOT "names a paper that exists in the literature". A
reader can't verify the reference: there is no DOI, no arXiv id, no URL — and the paper ships
its references as an inline ``thebibliography`` of bare titles.

This module closes that half of the chain. For each citation it searches the REAL
bibliographic providers (openalex / arxiv / semanticscholar — the same adapters Stage A
already uses) for a record whose title matches, and on a strong match attaches the DOI / URL /
year. It then emits:
  * ``paper/references.bib`` — a BibTeX file (the verifiable artifact a reader/CI can resolve),
    resolved entries carrying their ``doi``/``url``/``year``; unresolved ones kept but tagged
    ``note = {UNVERIFIED: no matching record found}`` so the gap is visible, not hidden.
  * ``paper/references.json`` — the evidence map (citation → matched record / UNRESOLVED).

Design decisions (mirroring verify/claims.py so the two read as one family):
  * The network edge (the providers) is INJECTED, never imported here. Tests pass fakes; the
    CLI passes the real registry adapters. With no providers (offline) we still emit a
    references.bib — every entry simply tagged UNVERIFIED — so the artifact always exists.
  * Matching is on a NORMALISED title (lowercase, alphanumeric, collapsed space) scored by the
    max of difflib ratio and token-set Jaccard; a match must clear ``MATCH_THRESHOLD``. This
    tolerates the wiki's paraphrased titles without accepting an unrelated paper.
  * Resolution is FLAG, not BLOCK (like claims v1): an unresolved citation is reported and can
    become a change-request, but does not by itself fail the build.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# A citation title resolves to a record when their normalised-title similarity clears this.
# 0.82 accepts light paraphrase / sub-title drift but rejects a merely topically-related paper.
MATCH_THRESHOLD = 0.82

# How many results to pull per provider when looking for a title match.
SEARCH_K = 6


@dataclass
class Resolution:
    """One citation after trying to resolve it against the real bibliographic providers."""

    key: str
    query_title: str  # the citation text we searched for
    resolved: bool = False
    matched_title: str = ""  # the provider record's title (for the evidence map)
    score: float = 0.0  # title-similarity of the accepted match
    source: str = ""  # which provider matched (openalex | arxiv | ...)
    doi: str = ""
    url: str = ""
    year: int | None = None
    record_id: str = ""  # the provider's own id (openalex work id / arxiv id)


@dataclass
class BibReport:
    """The evidence map written to paper/references.json."""

    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.resolutions)

    @property
    def n_resolved(self) -> int:
        return sum(1 for r in self.resolutions if r.resolved)

    @property
    def n_unresolved(self) -> int:
        return self.n_total - self.n_resolved

    def to_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_resolved": self.n_resolved,
            "n_unresolved": self.n_unresolved,
            "resolutions": [asdict(r) for r in self.resolutions],
        }


# --- title normalisation + matching -------------------------------------------
def _norm(title: str) -> str:
    """Lowercase, drop non-alphanumerics, collapse whitespace — so 'LLM-based, Repair!' and
    'llm based repair' compare equal-ish."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split())


def title_similarity(a: str, b: str) -> float:
    """Similarity of two titles in [0,1] — max of a sequence ratio and token-set Jaccard.

    The Jaccard term rewards heavy word overlap even when order/length differs (a subtitle was
    added/dropped); the sequence ratio rewards near-identical strings. Taking the max means
    either signal alone can confirm a match."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(ratio, jaccard)


def _doi_from_url(url: str | None) -> str:
    """Extract a bare DOI (10.xxxx/…) from a URL or doi: string, else ''."""
    if not url:
        return ""
    m = re.search(r"10\.\d{4,9}/\S+", url)
    return m.group(0) if m else ""


# --- resolution (network edge, providers INJECTED) ----------------------------
async def resolve_citation(citation, providers, *, k: int = SEARCH_K) -> Resolution:
    """Search every provider for the citation's title and return the best title match above
    MATCH_THRESHOLD as a Resolution. ``providers`` is a list of objects with an async
    ``search(query, k)`` returning store.models.Paper — injected, never imported, so this is
    offline-testable with fakes and degrades to UNRESOLVED when the list is empty."""
    res = Resolution(key=getattr(citation, "key", ""), query_title=getattr(citation, "text", ""))
    best_score, best = 0.0, None
    for prov in providers or []:
        try:
            papers = await prov.search(res.query_title, k=k)
        except Exception:
            continue  # a dead source degrades resolution, never crashes the run
        for p in papers:
            score = title_similarity(res.query_title, getattr(p, "title", "") or "")
            if score > best_score:
                best_score, best = score, p
    if best is not None and best_score >= MATCH_THRESHOLD:
        url = getattr(best, "url", "") or ""
        res.resolved = True
        res.matched_title = getattr(best, "title", "") or ""
        res.score = round(best_score, 4)
        res.source = getattr(best, "source", "") or ""
        res.doi = _doi_from_url(url)
        res.url = url
        res.year = getattr(best, "year", None)
        res.record_id = getattr(best, "id", "") or ""
    return res


async def resolve_citations(citations, providers, *, k: int = SEARCH_K) -> list[Resolution]:
    """Resolve each citation in turn (sequential — these adapters are politeness-rate-limited)."""
    return [await resolve_citation(c, providers, k=k) for c in (citations or [])]


# --- BibTeX emission (pure) ---------------------------------------------------
def _bib_escape(text: str) -> str:
    """Escape the BibTeX-significant characters in a free-text field value."""
    return (
        (text or "")
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )


def _entry(citation, res: Resolution | None) -> str:
    """Render one BibTeX entry for a citation, enriched by its resolution if any."""
    key = (getattr(citation, "key", "") or "ref").strip() or "ref"
    title = _bib_escape(getattr(citation, "text", "") or "Untitled")
    fields = [f"  title = {{{title}}}"]
    etype = "misc"
    if res and res.resolved:
        etype = "article" if res.doi else "misc"
        if res.year:
            fields.append(f"  year = {{{res.year}}}")
        if res.doi:
            fields.append(f"  doi = {{{_bib_escape(res.doi)}}}")
        if res.url:
            fields.append(f"  url = {{{_bib_escape(res.url)}}}")
        if res.source:
            fields.append(f"  note = {{resolved via {_bib_escape(res.source)}}}")
    else:
        fields.append("  note = {UNVERIFIED: no matching record found}")
    return f"@{etype}{{{key},\n" + ",\n".join(fields) + "\n}"


def to_bibtex(citations, resolutions: list[Resolution] | None = None) -> str:
    """Emit a complete references.bib from the citations, enriched by their resolutions.

    Pure (no I/O, no network) so it is the unit-testable core. A citation with no resolution —
    or an unresolved one — still gets an entry, tagged UNVERIFIED, so the bib is never silently
    short of the citations the paper actually uses."""
    by_key = {r.key: r for r in (resolutions or [])}
    header = (
        "% references.bib — generated by research-council (plan/25 Gap 2)\n"
        "% Entries tagged 'UNVERIFIED' had no matching record in the bibliographic providers.\n"
    )
    entries = [_entry(c, by_key.get(getattr(c, "key", ""))) for c in (citations or [])]
    return header + "\n".join(entries) + ("\n" if entries else "")


# --- artifact + loop integration ----------------------------------------------
async def write_bib(out_dir: Path | str, draft, providers=None, *, k: int = SEARCH_K) -> BibReport:
    """Resolve the draft's citations against ``providers`` (may be None/empty → all UNVERIFIED),
    write <out_dir>/paper/references.bib + references.json, and return the BibReport.

    Always writes the .bib (even with zero citations → just the header) so the artifact's
    presence/absence is a reliable signal rather than depending on whether resolution ran."""
    citations = list(getattr(draft, "citations", []) or [])
    resolutions = await resolve_citations(citations, providers, k=k)
    report = BibReport(resolutions=resolutions)

    paper = Path(out_dir) / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    (paper / "references.bib").write_text(to_bibtex(citations, resolutions), encoding="utf-8")
    (paper / "references.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def bib_to_change_requests(report: BibReport, *, severity: str = "low"):
    """Turn each UNRESOLVED citation into a change-request so the writing loop can demand the
    writer replace it with a verifiable reference or drop it. Default severity is *low*
    (flag-not-block) — promote once writer compliance is observed (mirrors claims v1)."""
    from research_council.store.models import ChangeRequest

    out = []
    for r in report.resolutions:
        if r.resolved:
            continue
        out.append(
            ChangeRequest(
                section="Related Work",
                severity=severity,
                msg=(
                    f"Citation [{r.key}] '{r.query_title}' could not be resolved to a real record "
                    f"in the bibliographic providers (no DOI/arXiv id). Replace it with a "
                    f"verifiable reference or remove the claim that relies on it."
                ),
            )
        )
    return out
