"""Typed data contracts for a debate. Canonical spec: plan/6 (Vertical Slice)."""

from __future__ import annotations

from pydantic import BaseModel, Field


# --- knowledge / retrieval -------------------------------------------------
class Paper(BaseModel):
    id: str
    title: str
    abstract: str = ""
    year: int | None = None
    url: str | None = None
    source: str = "unknown"  # provenance: wiki | openalex | arxiv | ...


# --- per-stage debate records ---------------------------------------------
class ResearchBrief(BaseModel):
    vendor: str
    landscape: str
    gap: str
    rationale: str = ""
    refs: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    id: str  # unique per debate; we use the authoring vendor seat
    vendor: str
    title: str
    gap: str
    hypothesis: str
    method: str
    experiment_plan: str
    refs: list[str] = Field(default_factory=list)
    version: int = 1


class Critique(BaseModel):
    critic_vendor: str
    target_id: str  # the (possibly anonymized) candidate label being critiqued
    axis: str  # novelty | soundness | feasibility
    severity: int = 1  # 1 (nit) .. 5 (fatal)
    claim: str = ""
    needs_verification: bool = False
    evidence_ref: str | None = None


class Rebuttal(BaseModel):
    candidate_id: str
    notes: str = ""
    revised: bool = False


class VerifierSignal(BaseModel):
    candidate_id: str
    runnable: bool
    feasibility: float = 0.0  # 0..1
    log: str = ""


class Score(BaseModel):
    judge_vendor: str
    candidate_id: str  # anonymized label at scoring time
    novelty: float = 0.0
    soundness: float = 0.0
    feasibility: float = 0.0
    clarity: float = 0.0
    rationale: str = ""


class Recommendation(BaseModel):
    ranked: list[str] = Field(default_factory=list)  # candidate ids, best first
    composites: dict[str, float] = Field(default_factory=dict)
    verifier_weighted: bool = True
    rationale: str = ""


# --- run config & trace envelope ------------------------------------------
DEFAULT_WEIGHTS = {"novelty": 0.35, "soundness": 0.25, "feasibility": 0.25, "clarity": 0.15}


class RunConfig(BaseModel):
    stage: str = "ideation"
    n_rounds: int = 2
    seats: dict[str, str] = Field(  # vendor -> model
        default_factory=lambda: {
            "openai": "gpt-5",
            "anthropic": "claude-opus-4-8",
            "gemini": "gemini-2.5-pro",
        }
    )
    tools: list[str] = Field(default_factory=lambda: ["wiki", "openalex"])
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    usd_max: float = 5.0
    anonymize: bool = True
    verifier_mode: str = "mock"  # mock (incr 1) | sandbox (incr 2)


class Event(BaseModel):
    """One line in the run trace. Also the observability + eval datum."""

    run_id: str
    ts: str
    phase: str
    round: int = 0
    author_vendor: str | None = None
    kind: str
    payload: dict = Field(default_factory=dict)
