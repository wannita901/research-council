"""Stage B→C approval gate (plan/25 Gap 4).

The product's second headline promise is "nothing advances without your approval" — but the
council also produces its OWN approval signal: each RQ's reviewers either approve the
experiment or not, recorded in ``experiment/results.csv`` (the ``approved`` column). Until
now that signal was written and then ignored: Stage C drafts a complete, venue-scored paper
even when *zero* RQs were approved (observed in project …103845: all RQs ``approved=False``,
yet a full paper was written as if it were a result).

This module reads that signal back *offline* (no API keys) so the writing stage can stop the
council from overclaiming on unapproved evidence. Two levers, mirroring the claims gate
(Gap 1) so the design is consistent:

  * ALWAYS — when not every RQ is approved, ``honesty_constraint`` returns framing the writer
    is told to obey (report unapproved RQs as feasibility/negative results, never as
    confirmed wins). This blocks overclaiming at the source without new templates.
  * CAP-GATED — when ``unapproved_block`` is on AND zero RQs are approved, the paper cannot
    ship as ``accepted``; ``approval_to_change_request`` injects a high-severity demand and the
    loop falls back to best-so-far. Default off (flag-not-block), on in the ``thorough`` profile.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

# Truthy spellings of the results.csv ``approved`` column (written as a Python bool repr, but
# be liberal: a hand-edited or differently-serialized CSV may use 1/yes/true).
_TRUE = {"true", "1", "yes", "y", "approved"}


@dataclass
class ApprovalStatus:
    """The council's own approval tally, read back from results.csv."""

    approved: int  # RQs the reviewers approved
    total: int  # RQs that ran (rows in results.csv)
    unapproved_rqs: list[str]  # rq_ids that ran but were not approved

    @property
    def any_approved(self) -> bool:
        return self.approved > 0

    @property
    def all_approved(self) -> bool:
        return self.total > 0 and self.approved == self.total

    @property
    def has_results(self) -> bool:
        return self.total > 0

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "total": self.total,
            "unapproved_rqs": list(self.unapproved_rqs),
        }


def approval_status(out_dir: Path | str) -> ApprovalStatus:
    """Read <out_dir>/experiment/results.csv and tally how many RQs the council approved.

    Uses the csv module (NOT cut -d,) because the ``question`` column contains commas inside
    quotes — the same footgun called out for the claims checker. A missing file yields an
    all-zero status (has_results == False), which callers treat as "no signal, don't gate"."""
    path = Path(out_dir) / "experiment" / "results.csv"
    if not path.exists():
        return ApprovalStatus(approved=0, total=0, unapproved_rqs=[])
    approved = 0
    total = 0
    unapproved: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            rq_id = (row.get("rq_id") or "").strip()
            if (row.get("approved") or "").strip().lower() in _TRUE:
                approved += 1
            else:
                unapproved.append(rq_id)
    return ApprovalStatus(approved=approved, total=total, unapproved_rqs=unapproved)


def feasibility_by_rq(out_dir: Path | str) -> dict[str, bool]:
    """Map each RQ id → whether its experiment was FEASIBLE (ran to exit 0 + emitted a METRIC),
    read from <out_dir>/experiment/results.csv. Empty when there's no results.csv (no signal).

    The writing stage uses this to keep figures from NON-feasible runs out of the paper: a
    non-feasible RQ's script errored or never produced a valid metric, so any plot it left on
    disk is from a broken run and must not be presented to the reader as a result."""
    path = Path(out_dir) / "experiment" / "results.csv"
    out: dict[str, bool] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rq_id = (row.get("rq_id") or "").strip()
            if rq_id:
                out[rq_id] = (row.get("feasible") or "").strip().lower() in _TRUE
    return out


def honesty_constraint(status: ApprovalStatus) -> str | None:
    """Framing the writer must obey when the council did not approve every RQ.

    Returns None when there's no results signal or everything was approved (no extra
    constraint needed). Otherwise returns a directive injected into the writer's constraints
    so the draft reports unapproved RQs honestly instead of as confirmed findings."""
    if not status.has_results or status.all_approved:
        return None
    if not status.any_approved:
        return (
            f"NONE of the {status.total} research question(s) were approved by the review "
            "council (approved=False in results.csv). Do NOT present any result as a confirmed "
            "or positive finding. Frame the paper as a feasibility / negative-result study: "
            "state plainly what was attempted, that the evidence did not meet the approval bar, "
            "and what would be needed to obtain a sound result. Do not overclaim."
        )
    return (
        f"Only {status.approved} of {status.total} research question(s) were approved by the "
        f"review council; the rest ({', '.join(status.unapproved_rqs)}) were not. Report each "
        "unapproved RQ as inconclusive / a negative result, not as a confirmed finding, and "
        "scope the contribution claims to the approved RQ(s) only."
    )


def approval_to_change_request(status: ApprovalStatus):
    """A high-severity ChangeRequest demanding the paper stop overclaiming when ZERO RQs were
    approved. Returns None otherwise. Used only when ``unapproved_block`` is on, mirroring how
    an unbacked numeric claim becomes a blocking change-request in the claims gate."""
    if not status.has_results or status.any_approved:
        return None
    from research_council.store.models import ChangeRequest

    return ChangeRequest(
        section="Results",
        severity="high",
        msg=(
            f"The review council approved 0 of {status.total} research question(s) "
            "(approved=False in results.csv). This paper cannot be accepted while it presents "
            "unapproved experiments as positive results. Reframe as a feasibility/negative "
            "result and remove any claim of a confirmed finding."
        ),
    )
