"""Claims-to-evidence checker (plan/25 Gap 1).

The product's headline promise is "every claim must cite a source or show experiment
evidence", but until now numeric claims in the prose were never diffed against the data.
A writer could state "accuracy falls from 0.81 to 0.57" with no such number anywhere in
``results.csv`` and nothing failed (observed in project …103845).

This module closes that gap *offline* (no API keys): it extracts the numeric claims the
paper makes about its OWN findings, matches each against the evidence values recorded in
``experiment/results.csv`` (rounding-aware, since authors round), and emits a
``paper/claims.json`` evidence map (claim → matched metric / UNBACKED). UNBACKED claims can
then be routed back into the writing loop as change-requests (``claims_to_change_requests``).

Design decisions (resolving the open questions in plan/25 §5):
  * Matching is ROUNDING-AWARE: ``F=5.08`` backs against ``5.0812`` because round(5.0812, 2)
    == 5.08. We also accept a small relative tolerance and unit-aware percent matching.
  * We audit only the sections where the paper reports its OWN results (Results + Abstract by
    default). Numbers in Related Work legitimately come from cited papers, not ``results.csv``,
    so checking them would produce false positives.
  * v1 is FLAG, not BLOCK: unbacked claims become *medium*-severity change-requests, so they
    surface in review.md / claims.json without destabilising the revision loop. Promote to
    blocking once writer compliance is observed.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Sections that state the paper's own empirical findings. Related Work / Introduction numbers
# usually come from cited prior work and must NOT be required to appear in results.csv.
DEFAULT_AUDITED_SECTIONS = ("Abstract", "Results")

# A reported number backs an evidence value if EITHER holds:
#   * the evidence value rounded to the claim's displayed precision equals the claim, or
#   * |claim - value| <= REL_TOL * |value|  (catches e.g. 0.390 vs 0.3900).
REL_TOL = 0.02

# Superscript digits used in scientific notation like 1.37×10⁻⁴.
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


@dataclass
class Evidence:
    """One measured value the experiment recorded (a results.csv row)."""

    metric: str
    value: float
    rq_id: str = ""
    raw: str = ""


@dataclass
class NumericClaim:
    """A number the prose asserts, with where it was found and its displayed precision."""

    text: str  # the literal token, e.g. "0.81" or "1.37×10⁻⁴" or "57%"
    value: float  # parsed numeric value (percent normalised to its as-written magnitude)
    section: str
    context: str  # ± a few words around the token, for the report
    decimals: int  # significant decimals as written (drives rounding-aware match)
    is_percent: bool = False


@dataclass
class CheckedClaim:
    """A claim after matching against the evidence set."""

    text: str
    value: float
    section: str
    context: str
    backed: bool
    matched_metric: str = ""
    matched_value: float | None = None


@dataclass
class ClaimReport:
    """The evidence map written to paper/claims.json."""

    backed: list[CheckedClaim] = field(default_factory=list)
    unbacked: list[CheckedClaim] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    audited_sections: list[str] = field(default_factory=list)

    @property
    def n_claims(self) -> int:
        return len(self.backed) + len(self.unbacked)

    @property
    def n_unbacked(self) -> int:
        return len(self.unbacked)

    def to_dict(self) -> dict:
        return {
            "n_claims": self.n_claims,
            "n_backed": len(self.backed),
            "n_unbacked": self.n_unbacked,
            "audited_sections": self.audited_sections,
            "backed": [asdict(c) for c in self.backed],
            "unbacked": [asdict(c) for c in self.unbacked],
            "evidence": [asdict(e) for e in self.evidence],
        }


# --- evidence loading ---------------------------------------------------------
def load_evidence(out_dir: Path | str) -> list[Evidence]:
    """Read the (metric, value) pairs from <out_dir>/experiment/results.csv.

    Uses csv (NOT cut -d,) because RQ ``question`` text contains commas inside quotes —
    a footgun called out in plan/25. Non-numeric / blank values are skipped."""
    import csv

    path = Path(out_dir) / "experiment" / "results.csv"
    if not path.exists():
        return []
    out: list[Evidence] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            raw = (row.get("value") or "").strip()
            val = _parse_number(raw)
            if val is None:
                continue
            out.append(
                Evidence(
                    metric=(row.get("metric") or "").strip(),
                    value=val,
                    rq_id=(row.get("rq_id") or "").strip(),
                    raw=raw,
                )
            )
    return out


# --- number parsing -----------------------------------------------------------
def _parse_number(token: str) -> float | None:
    """Parse a numeric token, including scientific notation written as 1.37×10⁻⁴,
    1.37 x 10^-4, or 1.37e-4. Returns None if it isn't a number."""
    if token is None:
        return None
    s = token.strip().translate(_SUPERSCRIPT).replace("−", "-")  # unicode minus → ascii
    s = s.replace(",", "")  # thousands separators
    # ×10^k / x10k / *10^-4  →  e-notation
    m = re.fullmatch(
        r"([+-]?\d+(?:\.\d+)?)\s*[×x*]\s*10\s*\^?\s*([+-]?\d+)", s, flags=re.IGNORECASE
    )
    if m:
        s = f"{m.group(1)}e{m.group(2)}"
    try:
        return float(s)
    except ValueError:
        return None


# Tokens that look like the paper's own measured quantities: decimals, percentages, and
# scientific notation. Bare integers (N=16, T=8, "3 metrics", years) are deliberately NOT
# matched — they are almost always design parameters or counts, not empirical results, and
# matching them floods the report with false positives.
_SCI = r"\d+(?:\.\d+)?\s*[×x*]\s*10\s*\^?\s*[⁻⁺+\-]?[⁰¹²³⁴⁵⁶⁷⁸⁹\d]+"
_PERCENT = r"\d+(?:\.\d+)?\s*%"
_DECIMAL = r"\d+\.\d+"
_NUM_RE = re.compile(rf"(?P<sci>{_SCI})|(?P<pct>{_PERCENT})|(?P<dec>{_DECIMAL})")

# Significance thresholds (p<0.05, α=0.01, p = .05) are conventions, not measured results;
# requiring them to appear in results.csv would be a false positive. Detected by the marker
# immediately preceding the number.
_THRESHOLD_RE = re.compile(r"(?:\bp\b|α|alpha)\s*[<>=≤≥]\s*$", re.IGNORECASE)


def _decimals_of(token: str) -> int:
    """Count significant decimals as written, so a rounded claim matches a precise value."""
    t = token.translate(_SUPERSCRIPT)
    m = re.search(r"\.(\d+)", t)
    return len(m.group(1)) if m else 0


# --- claim extraction ---------------------------------------------------------
def split_sections(paper_md: str) -> dict[str, str]:
    """Split a paper.md (## Section headers) into {name: body}. The leading '# Title' and
    its byline are ignored. Mirrors the layout written by debate.writing._write_paper."""
    sections: dict[str, str] = {}
    name, buf = None, []
    for ln in paper_md.splitlines():
        if ln.startswith("## "):
            if name is not None:
                sections[name] = "\n".join(buf).strip()
            name, buf = ln[3:].strip(), []
        elif name is not None:
            buf.append(ln)
    if name is not None:
        sections[name] = "\n".join(buf).strip()
    return sections


def extract_claims(text: str, section: str) -> list[NumericClaim]:
    """Pull numeric claims (decimals, percentages, scientific notation) from one section."""
    claims: list[NumericClaim] = []
    for m in _NUM_RE.finditer(text):
        token = m.group(0).strip()
        if _THRESHOLD_RE.search(text[max(0, m.start() - 8) : m.start()]):
            continue  # p<0.05 / α=0.01 — a significance threshold, not a measured result
        is_pct = m.lastgroup == "pct"
        value = _parse_number(token.rstrip("%").strip() if is_pct else token)
        if value is None:
            continue
        lo, hi = max(0, m.start() - 40), min(len(text), m.end() + 40)
        ctx = " ".join(text[lo:hi].split())
        claims.append(
            NumericClaim(
                text=token,
                value=value,
                section=section,
                context=ctx,
                decimals=_decimals_of(token),
                is_percent=is_pct,
            )
        )
    return claims


# --- matching -----------------------------------------------------------------
def _matches(claim_value: float, decimals: int, ev_value: float) -> bool:
    """Rounding-aware match: the claim backs the evidence if rounding the evidence to the
    claim's displayed precision reproduces the claim, or they agree within REL_TOL."""
    if round(ev_value, decimals) == round(claim_value, decimals):
        return True
    scale = abs(ev_value) if ev_value else 1.0
    return abs(claim_value - ev_value) <= REL_TOL * scale


def match_claim(claim: NumericClaim, evidence: list[Evidence]) -> Evidence | None:
    """Find an evidence value that backs this claim. Percent claims also try the fractional
    form (57% ↔ 0.57) since metrics are often stored as fractions."""
    candidates = [claim.value]
    if claim.is_percent:
        candidates.append(claim.value / 100.0)
    for ev in evidence:
        for cv in candidates:
            dec = max(claim.decimals, claim.decimals + (2 if claim.is_percent else 0))
            if _matches(cv, dec, ev.value):
                return ev
    return None


def draft_to_audit_md(draft, audited_sections: tuple[str, ...] = DEFAULT_AUDITED_SECTIONS) -> str:
    """Render the audited sections of an in-memory PaperDraft into the same ``## Section``
    markdown that ``check_paper`` consumes. The draft keeps the abstract on its own field and
    the rest in ``.sections``; this stitches them back so the loop can check a draft BEFORE it
    is written to disk (paper.md only exists after the writing loop finishes)."""
    parts: list[str] = []
    for name in audited_sections:
        body = (draft.abstract if name == "Abstract" else draft.sections.get(name, "")) or ""
        if body.strip():
            parts.append(f"## {name}\n{body}")
    return "\n\n".join(parts)


def check_draft(
    draft,
    evidence: list[Evidence],
    *,
    audited_sections: tuple[str, ...] = DEFAULT_AUDITED_SECTIONS,
) -> ClaimReport:
    """Run the claims-to-evidence check against a live PaperDraft (not a file). Used inside the
    writing loop so unbacked numeric claims can be folded into the round's change-requests and
    actually drive a revision, instead of only being reported after the fact."""
    return check_paper(
        draft_to_audit_md(draft, audited_sections), evidence, audited_sections=audited_sections
    )


def check_paper(
    paper_md: str,
    evidence: list[Evidence],
    *,
    audited_sections: tuple[str, ...] = DEFAULT_AUDITED_SECTIONS,
) -> ClaimReport:
    """Extract claims from the audited sections and classify each as backed / unbacked."""
    sections = split_sections(paper_md)
    report = ClaimReport(evidence=list(evidence), audited_sections=list(audited_sections))
    for name in audited_sections:
        body = sections.get(name, "")
        if not body:
            continue
        for claim in extract_claims(body, name):
            ev = match_claim(claim, evidence)
            checked = CheckedClaim(
                text=claim.text,
                value=claim.value,
                section=claim.section,
                context=claim.context,
                backed=ev is not None,
                matched_metric=ev.metric if ev else "",
                matched_value=ev.value if ev else None,
            )
            (report.backed if ev else report.unbacked).append(checked)
    return report


# --- artifact + loop integration ----------------------------------------------
def write_claims_report(
    out_dir: Path | str, *, audited_sections: tuple[str, ...] = DEFAULT_AUDITED_SECTIONS
) -> ClaimReport | None:
    """Load <out_dir>/paper/paper.md + experiment/results.csv, run the check, and write
    <out_dir>/paper/claims.json. Returns the report, or None if paper.md is absent."""
    paper_md = Path(out_dir) / "paper" / "paper.md"
    if not paper_md.exists():
        return None
    evidence = load_evidence(out_dir)
    report = check_paper(
        paper_md.read_text(encoding="utf-8"), evidence, audited_sections=audited_sections
    )
    (Path(out_dir) / "paper" / "claims.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def claims_to_change_requests(report: ClaimReport, *, severity: str = "medium"):
    """Turn each UNBACKED claim into a section-tagged ChangeRequest so the writing loop can
    demand the writer cite it or delete it. v1 default severity is *medium* (non-blocking)."""
    from research_council.store.models import ChangeRequest

    out = []
    for c in report.unbacked:
        out.append(
            ChangeRequest(
                section=c.section,
                severity=severity,
                msg=(
                    f"Unbacked numeric claim '{c.text}' in {c.section} has no matching value "
                    f'in results.csv (context: "{c.context}"). Cite a source for it, back it '
                    f"with a recorded metric, or remove it."
                ),
            )
        )
    return out
