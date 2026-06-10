"""Authorship anonymization — the core self-preference-bias control (plan/2 §4).

Labels are assigned in a SHUFFLED order so a candidate's position can't leak its author
(otherwise C1/C2/C3 always map to the same vendor seats). Pass `seed` for reproducibility.
"""

from __future__ import annotations

import random

from research_council.store.models import Candidate


def anonymize(candidates: list[Candidate], on: bool = True,
              *, seed: int | None = None) -> tuple[list[dict], dict[str, Candidate]]:
    """Return (anon views, label->candidate map). When off, labels are the real ids."""
    order = list(range(len(candidates)))
    if on:
        random.Random(seed).shuffle(order)  # seed=None → nondeterministic per run
    anon: list[dict] = []
    id_map: dict[str, Candidate] = {}
    for label_i, ci in enumerate(order, 1):
        c = candidates[ci]
        label = f"C{label_i}" if on else c.id
        id_map[label] = c
        anon.append({
            "label": label,
            "title": c.title if on else f"[{c.vendor}] {c.title}",
            "gap": c.gap,
            "problem_statement": c.problem_statement,
            "motivation": c.motivation,
            "hypothesis": c.hypothesis,
            "method": c.method,
            "experiment_plan": c.experiment_plan,
            "research_questions": [rq.model_dump() for rq in c.numbered_rqs()],
            "dataset_metrics": c.dataset_metrics,
            "fallback_plan": c.fallback_plan,
        })
    return anon, id_map
