"""FastAPI backend — the service runner over the same debate core (plan/11).

POST /debates              start a debate (background task) → {run_id}
GET  /debates/{id}/stream  live SSE Event stream (watch the debate)
POST /debates/{id}/action  resolve the review gate (iterate|amend|conclude|select)
GET  /debates/{id}         status + recommendation

Per-run asyncio.Queue is the event bus (emit → queue → SSE); the reviewer awaits a
future resolved by POST /action. In-process, no Redis/Celery (v0).
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from research_council.agents.stub_peer import StubPeer
from research_council.config import load_config
from research_council.debate.orchestrator import run_debate
from research_council.retrieval.registry import build_retrieval, build_stub_retrieval
from research_council.store.checkpoint import TraceWriter
from research_council.store.models import Candidate, Event, Recommendation, ReviewAction
from research_council.verify.mock import MockVerifier

_SENTINEL = object()  # closes an SSE stream


class DebateRequest(BaseModel):
    topic: str
    stage: str = "ideation"
    seats: dict[str, str] | None = None
    tools: list[str] | None = None
    rounds: int | None = None
    anonymize: bool | None = None
    live: bool = False
    interactive: bool = True  # False → autonomous (auto-proceed, no review gate)


class ActionRequest(BaseModel):
    action: str = "iterate"  # iterate | amend | conclude | select
    choice: str | None = None
    feedback: str = ""


class Run:
    def __init__(self, cfg, topic: str, trace: TraceWriter):
        self.cfg = cfg
        self.topic = topic
        self.trace = trace
        self.run_id = trace.run_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.status = "running"  # running | awaiting_review | done | error
        self.error: str | None = None
        self.recommendation: Recommendation | None = None
        self.candidates: list[Candidate] = []
        self._review: asyncio.Future | None = None
        self.task: asyncio.Task | None = None

    def emit(self, ev: Event) -> None:
        self.queue.put_nowait(ev)

    async def reviewer(self, rec: Recommendation, candidates: list[Candidate], rnd: int) -> ReviewAction:
        self.recommendation, self.candidates = rec, candidates
        self.status = "awaiting_review"
        ev = self.trace.emit("review", "review_request", {
            "recommendation": rec.model_dump(),
            "candidates": [c.model_dump() for c in candidates],
        }, round=rnd)
        self.queue.put_nowait(ev)
        self._review = asyncio.get_running_loop().create_future()
        action = await self._review
        self._review = None
        self.status = "running"
        return action

    def submit_action(self, action: ReviewAction) -> bool:
        if self._review and not self._review.done():
            self._review.set_result(action)
            return True
        return False


def _build_peers(cfg, live: bool):
    if not live:
        return [StubPeer(v) for v in cfg.seats]
    from research_council.agents.llm_peer import LLMPeer
    from research_council.providers.sdk import build_provider
    return [LLMPeer(v, build_provider(v, m)) for v, m in cfg.seats.items()]


RUNS: dict[str, Run] = {}
app = FastAPI(title="research-council")


@app.get("/")
async def index():
    return {"service": "research-council", "runs": list(RUNS)}


@app.post("/debates")
async def start_debate(req: DebateRequest):
    cfg = load_config(req.stage)
    if req.seats:
        cfg.seats = req.seats
    if req.tools:
        cfg.tools = req.tools
    if req.rounds is not None:
        cfg.n_rounds = req.rounds
    if req.anonymize is not None:
        cfg.anonymize = req.anonymize

    peers = _build_peers(cfg, req.live)
    retrieval = build_retrieval(cfg.tools) if req.live else build_stub_retrieval(cfg.tools)
    run = Run(cfg, req.topic, TraceWriter.new(cfg.stage))
    RUNS[run.run_id] = run
    reviewer = run.reviewer if req.interactive else None
    run.task = asyncio.create_task(_run(run, peers, retrieval, MockVerifier(), reviewer))
    return {"run_id": run.run_id}


async def _run(run: Run, peers, retrieval, verifier, reviewer) -> None:
    try:
        rec, cands = await run_debate(
            run.cfg, run.topic, peers, retrieval, verifier, run.trace,
            emit=run.emit, reviewer=reviewer,
        )
        run.recommendation, run.candidates, run.status = rec, cands, "done"
    except Exception as e:  # surface failure to clients; never hang the run
        run.status, run.error = "error", str(e)
        try:
            run.trace.emit("error", "error", {"message": str(e)})
        except Exception:
            pass
    finally:
        run.queue.put_nowait(_SENTINEL)


def _get(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    return run


@app.get("/debates/{run_id}")
async def get_debate(run_id: str):
    run = _get(run_id)
    return {
        "run_id": run_id,
        "status": run.status,
        "error": run.error,
        "awaiting_review": run.status == "awaiting_review",
        "recommendation": run.recommendation.model_dump() if run.recommendation else None,
        "candidates": [c.model_dump() for c in run.candidates],
    }


@app.post("/debates/{run_id}/action")
async def post_action(run_id: str, req: ActionRequest):
    run = _get(run_id)
    ok = run.submit_action(ReviewAction(action=req.action, choice=req.choice, feedback=req.feedback))
    if not ok:
        raise HTTPException(409, "no pending review for this run")
    return {"ok": True}


@app.get("/debates/{run_id}/stream")
async def stream(run_id: str):
    run = _get(run_id)

    async def gen():
        while True:
            ev = await run.queue.get()
            if ev is _SENTINEL:
                yield "event: end\ndata: {}\n\n"
                break
            yield f"data: {ev.model_dump_json()}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
