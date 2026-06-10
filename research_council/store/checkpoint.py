"""Append-only JSONL trace per run. Doubles as checkpoint + eval data (plan/7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from research_council.store.models import Event


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TraceWriter:
    def __init__(self, run_id: str, path: Path):
        self.run_id = run_id
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def new(cls, stage: str, runs_dir: Path | str = "runs") -> TraceWriter:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        run_id = f"{stamp}-{stage}-{uuid.uuid4().hex[:6]}"
        return cls(run_id, Path(runs_dir) / run_id / "trace.jsonl")

    def emit(
        self,
        phase: str,
        kind: str,
        payload: dict,
        *,
        round: int = 0,
        author_vendor: str | None = None,
    ) -> Event:
        ev = Event(
            run_id=self.run_id,
            ts=_now(),
            phase=phase,
            round=round,
            author_vendor=author_vendor,
            kind=kind,
            payload=payload,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(ev.model_dump_json() + "\n")
        return ev
