"""Authorship anonymization — the core self-preference-bias control (plan/2 §4)."""

from __future__ import annotations

from research_council.store.models import Candidate


def anonymize(candidates: list[Candidate], on: bool = True) -> tuple[list[dict], dict[str, Candidate]]:
    """Return (anon views, label->candidate map). When off, labels are the real ids."""
    anon: list[dict] = []
    id_map: dict[str, Candidate] = {}
    for i, c in enumerate(candidates, 1):
        label = f"C{i}" if on else c.id
        id_map[label] = c
        anon.append({
            "label": label,
            "title": c.title if on else f"[{c.vendor}] {c.title}",
            "gap": c.gap,
            "method": c.method,
            "experiment_plan": c.experiment_plan,
        })
    return anon, id_map
