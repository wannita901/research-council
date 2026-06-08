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
    origin: str | None = None  # wiki only: "external" (prior art) | "internal" (council synthesis)


# --- per-stage debate records ---------------------------------------------
class ResearchBrief(BaseModel):
    vendor: str
    landscape: str
    gap: str
    rationale: str = ""
    refs: list[str] = Field(default_factory=list)


class BriefDraft(BaseModel):
    """Agent output for the research phase (vendor is set by the orchestrator, not the model)."""

    landscape: str = ""
    gap: str = ""
    rationale: str = ""
    refs: list[str] = Field(default_factory=list)


class CandidateDraft(BaseModel):
    """Agent output for the propose phase (id/vendor/gap set by the orchestrator)."""

    title: str = ""
    hypothesis: str = ""
    method: str = ""
    experiment_plan: str = ""


class ScoreItem(BaseModel):
    label: str
    novelty: float = 0.0
    soundness: float = 0.0
    feasibility: float = 0.0
    clarity: float = 0.0


class ScoreSheet(BaseModel):
    """Agent output for the judge phase (per anonymized candidate label)."""

    items: list[ScoreItem] = Field(default_factory=list)


class Candidate(BaseModel):
    id: str  # unique per debate; v2 uses the authoring codename
    vendor: str
    title: str
    gap: str
    hypothesis: str
    method: str
    experiment_plan: str
    refs: list[str] = Field(default_factory=list)
    version: int = 1


class Contribution(BaseModel):
    """One peer's turn in a deliberation (the agent's output)."""

    kind: str = "pass"  # critique | question | answer | defend | concede | revise | pass
    to: str | None = None  # codename addressed (for question/answer)
    content: str = ""
    refs: list[str] = Field(default_factory=list)
    targets: str | None = None  # candidate id this is about
    revision: CandidateDraft | None = None  # for kind="revise": updated fields of one's OWN candidate
    done: bool = False  # nothing substantive left to add


class DiscussionMessage(BaseModel):
    """A recorded deliberation message (Contribution + envelope)."""

    round: int = 0
    turn: int = 0
    from_codename: str
    kind: str
    to: str | None = None
    content: str = ""
    refs: list[str] = Field(default_factory=list)
    targets: str | None = None


class RoundDigest(BaseModel):
    """Structured memory carried into the next round's research (plan/15 #3)."""

    round: int = 0
    gaps: list[str] = Field(default_factory=list)         # "Codename: gap"
    candidates: list[str] = Field(default_factory=list)   # "id: title"
    top_critiques: list[str] = Field(default_factory=list)  # "from → target: claim"
    verifier: list[str] = Field(default_factory=list)     # grounding signals
    human_comment: str = ""


class IntakeQuestion(BaseModel):
    id: str = ""
    question: str
    why: str = ""


class IntakeQuestions(BaseModel):
    """Facilitator output wrapper (plan/15 #5)."""

    questions: list[IntakeQuestion] = Field(default_factory=list)


class Constraints(BaseModel):
    """Answers captured at a stage's intake; injected into the council's context."""

    stage: str = "ideation"
    answers: dict[str, str] = Field(default_factory=dict)  # question -> answer


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
    breakdown: dict[str, dict] = Field(default_factory=dict)  # id -> per-axis mean (transparency)
    verifier_weighted: bool = True
    rationale: str = ""


class ReviewAction(BaseModel):
    """Human decision at the round-boundary review gate (plan/11).

    action:
      iterate  — run another round (peers only, no human note)
      amend    — run another round + inject `feedback` as a human critique
      conclude — end now, take the panel's ranking (no human pick)
      select   — end now, `choice` is the winner (defaults to rank-1)
      auto     — default: defer to the autonomous termination policy
    """

    action: str = "auto"
    choice: str | None = None  # candidate id to select (or target of an amendment)
    feedback: str = ""  # injected into the next round as a human critique (amend)


# --- macro lifecycle (plan/13; Tier 2 #8 state machine, #9 cross-stage memory) ---
STAGES = ["ideation", "experimentation", "writing"]


class StageState(BaseModel):
    name: str
    status: str = "pending"  # pending | active | awaiting_approval | approved
    run_id: str | None = None
    summary: str = ""                               # short human-readable outcome
    artifacts: dict = Field(default_factory=dict)   # stage outputs (selected idea, results, …)


class StageHandoff(BaseModel):
    """What carries from one stage to the next (the cross-stage memory)."""

    from_stage: str
    to_stage: str
    idea: dict = Field(default_factory=dict)         # the selected candidate
    experiment_plan: str = ""
    constraints: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    artifacts: dict = Field(default_factory=dict)    # prior stage's raw outputs


class Project(BaseModel):
    id: str
    topic: str
    created: str = ""
    current: str = "ideation"
    constraints: dict[str, str] = Field(default_factory=dict)
    stages: dict[str, StageState] = Field(default_factory=dict)
    log: list[str] = Field(default_factory=list)


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
    # Intake facilitator (writes the clarifying questions) — a Claude model by default;
    # override via RC_FACILITATOR_MODEL in mise. Vendor is fixed to anthropic.
    facilitator_model: str = "claude-sonnet-4-6"
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    usd_max: float = 5.0
    anonymize: bool = True
    verifier_mode: str = "mock"  # mock (incr 1) | sandbox (incr 2)
    # v2 agentic caps (plan/15 #1)
    max_iters: int = 5         # per-peer research loop iterations
    max_tool_calls: int = 8    # per-peer tool calls
    max_turns: int = 4         # deliberation sub-rounds
    max_rounds: int = 4        # full ideation rounds


class Event(BaseModel):
    """One line in the run trace. Also the observability + eval datum."""

    run_id: str
    ts: str
    phase: str
    round: int = 0
    author_vendor: str | None = None
    kind: str
    payload: dict = Field(default_factory=dict)
