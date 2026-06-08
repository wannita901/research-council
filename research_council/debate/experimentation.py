"""Stage B engine (plan/13) — implement → run-in-sandbox → verify, with bounded retry.

The coder drafts a minimal script; the sandbox runs it; feasibility = it executed AND
printed a `METRIC <name>=<value>` line. On failure the error is fed back to the coder and
we retry up to `max_attempts`. Returns a typed ExperimentResult.
"""

from __future__ import annotations

import re
from typing import Callable

from research_council.store.models import ExperimentResult

_METRIC = re.compile(r"METRIC\s+([^\s=]+)\s*=\s*(\S+)")


async def run_experimentation(idea: dict, plan: str, coder, sandbox, *,
                              max_attempts: int = 2, timeout: int = 30,
                              emit: Callable[[str, str, dict], None] | None = None) -> ExperimentResult:
    last_err, code, last_ran = "", "", False
    for attempt in range(1, max_attempts + 1):
        draft = await coder.draft(idea, plan, error=last_err)
        code = draft.code
        if emit:
            emit("experiment", "code_drafted", {"attempt": attempt, "chars": len(code), "notes": draft.notes})
        res = sandbox.run(code, timeout=timeout)
        last_ran = res.ok
        if emit:
            emit("experiment", "sandbox_run",
                 {"attempt": attempt, "ok": res.ok, "exit_code": res.exit_code,
                  "timed_out": res.timed_out, "backend": res.backend})
        m = _METRIC.search(res.stdout or "")
        if res.ok and m:
            return ExperimentResult(ran=True, feasible=True, metric=f"{m.group(1)}={m.group(2)}",
                                    attempts=attempt, code=code, log=(res.stdout or "")[-1200:],
                                    backend=res.backend)
        last_err = (res.stderr or res.stdout or f"exit {res.exit_code}")[-1000:]
    return ExperimentResult(ran=last_ran, feasible=False, metric=None, attempts=max_attempts,
                            code=code, log=last_err, backend=getattr(sandbox, "name", ""))
