"""Typed data contracts for a debate. Canonical spec: plan/6 (Vertical Slice)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Placeholder written as experiment.py when an RQ produced no code. The reproduction manifest
# must hash these exact bytes (not ""), or check_code_integrity flags a spurious sha256 mismatch
# against the on-disk file in precisely the no-code case this placeholder anticipates.
NO_CODE_PLACEHOLDER = "# no code produced\n"


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


class ResearchQuestion(BaseModel):
    """One research question in the proposal — its own focused experiment + metric(s).
    Stage B runs one council loop per RQ (id assigned by the orchestrator if blank)."""

    id: str = ""  # rq1, rq2, … (assigned downstream)
    question: str = ""
    plan: str = ""  # step-by-step plan to answer THIS question
    metrics: str = ""  # metric(s) that answer THIS question


class CandidateDraft(BaseModel):
    """Agent output for the propose phase — a full research proposal the council argues over
    (id/vendor/gap set by the orchestrator). Empty fields in a `revise` draft mean 'unchanged'."""

    title: str = ""
    problem_statement: str = ""  # the concrete problem being addressed
    motivation: str = ""  # why it matters now (impact)
    hypothesis: str = ""
    method: str = ""  # the proposed method / approach
    experiment_plan: str = ""  # overall step-by-step plan
    research_questions: list[ResearchQuestion] = Field(default_factory=list)  # per-RQ experiments
    dataset_metrics: str = ""  # datasets + evaluation metrics
    fallback_plan: str = ""  # what to do if the main plan fails


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
    """A full research proposal — the Stage-A artifact the council argues over and that flows
    (via StageHandoff.idea) into Stage B (implement/improve the plan) and C (write the paper)."""

    id: str  # unique per debate; v2 uses the authoring codename
    vendor: str
    title: str
    gap: str  # the research gap (from the author's brief)
    hypothesis: str
    method: str
    experiment_plan: str
    problem_statement: str = ""
    motivation: str = ""
    research_questions: list[ResearchQuestion] = Field(default_factory=list)
    dataset_metrics: str = ""
    fallback_plan: str = ""
    refs: list[str] = Field(default_factory=list)
    version: int = 1

    def numbered_rqs(self) -> list[ResearchQuestion]:
        """Research questions with ids assigned (rq1, rq2, …). Falls back to a single RQ built
        from the overall experiment_plan when none were proposed (preserves single-experiment)."""
        if self.research_questions:
            return [
                rq.model_copy(update={"id": rq.id or f"rq{i}"})
                for i, rq in enumerate(self.research_questions, 1)
            ]
        return [
            ResearchQuestion(
                id="rq1",
                question=self.hypothesis or self.title,
                plan=self.experiment_plan,
                metrics=self.dataset_metrics,
            )
        ]

    def as_proposal_md(self) -> str:
        """Render the proposal as a markdown document (the Stage-A artifact)."""
        rows = [
            ("Problem Statement", self.problem_statement),
            ("Motivation", self.motivation),
            ("Hypothesis", self.hypothesis),
            ("Proposed Method", self.method),
            ("Step-by-step Experiment Plan", self.experiment_plan),
            ("Dataset / Metrics", self.dataset_metrics),
            ("Fallback Plan", self.fallback_plan),
        ]
        body = [f"# {self.title}", "", f"*Research gap:* {self.gap}", ""]
        for head, text in rows:
            body += [f"## {head}", (text or "_(not specified)_"), ""]
        if self.research_questions:
            body += ["## Research Questions"]
            for rq in self.numbered_rqs():
                body += [
                    f"### {rq.id.upper()}: {rq.question}",
                    f"- *Plan:* {rq.plan or '—'}",
                    f"- *Metrics:* {rq.metrics or '—'}",
                    "",
                ]
        if self.refs:
            body += ["## References", *[f"- {r}" for r in self.refs]]
        return "\n".join(body).strip() + "\n"


class Contribution(BaseModel):
    """One peer's turn in a deliberation (the agent's output)."""

    kind: str = "pass"  # critique | question | answer | defend | concede | revise | pass
    to: str | None = None  # codename addressed (for question/answer)
    content: str = ""
    refs: list[str] = Field(default_factory=list)
    targets: str | None = None  # candidate id this is about
    revision: CandidateDraft | None = (
        None  # for kind="revise": updated fields of one's OWN candidate
    )
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
    gaps: list[str] = Field(default_factory=list)  # "Codename: gap"
    candidates: list[str] = Field(default_factory=list)  # "id: title"
    top_critiques: list[str] = Field(default_factory=list)  # "from → target: claim"
    verifier: list[str] = Field(default_factory=list)  # grounding signals
    human_comment: str = ""


class OnboardingQuestion(BaseModel):
    id: str = ""
    question: str
    why: str = ""


class OnboardingQuestions(BaseModel):
    """Facilitator output wrapper (plan/15 #5)."""

    questions: list[OnboardingQuestion] = Field(default_factory=list)


class Constraints(BaseModel):
    """Answers captured at a stage's onboarding; injected into the council's context."""

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
    summary: str = ""  # short human-readable outcome
    artifacts: dict = Field(default_factory=dict)  # stage outputs (selected idea, results, …)


class StageHandoff(BaseModel):
    """What carries from one stage to the next (the cross-stage memory)."""

    from_stage: str
    to_stage: str
    idea: dict = Field(default_factory=dict)  # the selected candidate
    experiment_plan: str = ""
    constraints: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    artifacts: dict = Field(default_factory=dict)  # prior stage's raw outputs


class Project(BaseModel):
    id: str
    topic: str
    created: str = ""
    current: str = "ideation"
    constraints: dict[str, str] = Field(default_factory=dict)
    stages: dict[str, StageState] = Field(default_factory=dict)
    log: list[str] = Field(default_factory=list)


# --- Stage B · experimentation (#11, council loop plan/18) -------------------
class ExperimentDraft(BaseModel):
    """Coder agent output — a self-contained script + the pip packages it needs (plan/24).

    `requirements` are installed in a network-enabled prep step BEFORE the script runs; the
    script itself then runs with no network. Empty = standard library only."""

    code: str = ""
    notes: str = ""
    requirements: list[str] = Field(default_factory=list)  # e.g. ["numpy", "scikit-learn"]


# Review finding kinds; correctness/soundness at high severity block approval.
FINDING_KINDS = ("correctness", "soundness", "reproducibility", "overclaim", "style")
SEVERITIES = ("high", "medium", "low")


class VerificationProbe(BaseModel):
    """A short script a reviewer runs in the sandbox to substantiate/retract a finding."""

    code: str = ""
    ran: bool = False
    output: str = ""  # trimmed stdout/stderr
    supports: bool = False  # did the probe confirm the finding?


class ReviewFinding(BaseModel):
    kind: str = "style"  # one of FINDING_KINDS
    severity: str = "low"  # one of SEVERITIES
    msg: str = ""
    fix: str = ""  # concrete suggested fix
    probe: VerificationProbe | None = None

    @property
    def blocking(self) -> bool:
        return self.kind in ("correctness", "soundness") and self.severity == "high"


class CodeReview(BaseModel):
    """One reviewer's verdict on a code draft + its run."""

    reviewer_vendor: str = ""
    approve: bool = False
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: str = ""

    @property
    def has_blocker(self) -> bool:
        return any(f.blocking for f in self.findings)


class ExperimentResult(BaseModel):
    ran: bool = False  # the script executed without error (exit 0, no timeout)
    feasible: bool = False  # ran AND emitted a METRIC line — the "run-it" verification
    metric: str | None = None  # HEADLINE metric (first METRIC line) — drives feasibility/repro
    # ALL `METRIC name=value` lines the run printed, first-seen order (metric == metrics[0]).
    # Secondary metrics (baselines, per-cell/group values, ablations) are recorded so a paper's
    # non-headline numbers have a verifiable source in metrics.csv, not just the single point.
    metrics: list[str] = Field(default_factory=list)
    attempts: int = 0
    code: str = ""
    log: str = ""
    backend: str = ""  # docker | local
    # council-loop additions (plan/18)
    approved: bool = False  # feasible AND K-of-N reviewers approved
    approvals: int = 0  # reviewers who approved on the final iteration
    iterations: int = 0  # implement→run→review cycles spent
    reviews: list[CodeReview] = Field(default_factory=list)  # final-iteration reviews
    usd: float = 0.0  # spend on this stage
    stopped_reason: str = ""  # approved | iters_exhausted | budget_exhausted
    requirements: list[str] = Field(default_factory=list)  # pip deps the experiment used
    figures: list[str] = Field(default_factory=list)  # figure filenames the experiment saved
    # raw figure bytes — excluded from model_dump (kept out of project.json), written to disk
    # by write_experiments and consumed by Stage C.
    figures_data: dict[str, bytes] = Field(default_factory=dict, exclude=True, repr=False)


class RQResult(BaseModel):
    """One research question's experiment outcome (plan/21 — RQ-driven Stage B)."""

    rq_id: str = ""
    question: str = ""
    result: ExperimentResult = Field(default_factory=ExperimentResult)


# --- Stage C · writing (#12, venue rubric #10, council loop plan/18) ---------
class Citation(BaseModel):
    """A reference. `grounded` = drawn from an LLM-wiki origin:external page (trusted);
    otherwise it was search-augmented and is tagged needs-verification."""

    key: str = ""  # bibtex-style key
    text: str = ""  # human-readable reference
    source_id: str = ""  # wiki page id / url
    grounded: bool = True  # True → from the wiki prior-art corpus
    needs_verification: bool = False


class PaperDraft(BaseModel):
    """Writer agent output — a structured paper."""

    title: str = ""
    abstract: str = ""
    sections: dict[str, str] = Field(default_factory=dict)  # name -> markdown body
    citations: list[Citation] = Field(default_factory=list)
    figures: list[str] = Field(default_factory=list)  # relative paths to results figures (plan/24)


class ChangeRequest(BaseModel):
    """A reviewer's revision request, tagged by the section it touches (plan/18)."""

    section: str = ""  # which section to revise ("" = whole-paper / abstract)
    severity: str = "low"  # high | medium | low
    msg: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "high"


class ReviewNotes(BaseModel):
    """Reviewer agent output — scored against the venue rubric."""

    scores: dict[str, float] = Field(default_factory=dict)  # criterion -> 0..1
    comments: list[str] = Field(default_factory=list)
    verdict: str = ""  # e.g. accept | accept-with-revisions | reject
    change_requests: list[ChangeRequest] = Field(default_factory=list)
    reviewer_vendor: str = ""

    @property
    def mean(self) -> float:
        return round(sum(self.scores.values()) / len(self.scores), 4) if self.scores else 0.0

    @property
    def has_blocker(self) -> bool:
        return any(c.blocking for c in self.change_requests)


class VenueChoice(BaseModel):
    """Stage-C writing constraints — supplied by --venue or gathered at onboarding (plan/18)."""

    venue: str = "generic"
    emphasis: str = ""  # what to foreground (e.g. "the automation angle")
    double_blind: bool = False
    page_limit: int | None = None  # override the venue default
    rationale: str = ""  # if council-recommended, why this venue


class WritingResult(BaseModel):
    venue: str = ""
    title: str = ""
    paper_path: str = ""
    pdf_path: str = ""  # set if the LaTeX build succeeded
    sections: list[str] = Field(default_factory=list)
    review: ReviewNotes = Field(default_factory=ReviewNotes)
    score_history: list[float] = Field(default_factory=list)  # mean rubric score per round
    revisions: int = 0
    accepted: bool = False  # met the accept bar (vs best-so-far on exhaust)
    citations: list[Citation] = Field(default_factory=list)
    usd: float = 0.0
    stopped_reason: str = ""  # accepted | revisions_exhausted | budget_exhausted
    latex: str = ""  # built | fallback_no_tex | build_failed | skipped
    claims_total: int = 0  # numeric claims audited against results.csv (plan/25 Gap 1)
    claims_unbacked: int = 0  # of those, how many had no matching evidence value
    approved_rqs: int = 0  # RQs the council approved, read back from results.csv (plan/25 Gap 4)
    total_rqs: int = 0  # RQs that ran (approved_rqs of total_rqs gated the B→C handoff)
    refs_total: int = 0  # citations the paper uses, emitted to references.bib (plan/25 Gap 2)
    refs_resolved: int = 0  # of those, how many resolved to a real DOI/arXiv record


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
    # Onboarding facilitator (writes the clarifying questions) — a Claude model by default;
    # override via RC_FACILITATOR_MODEL in mise. Vendor is fixed to anthropic.
    facilitator_model: str = "claude-sonnet-4-6"
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    usd_max: float = 5.0
    anonymize: bool = True
    verifier_mode: str = "mock"  # mock (incr 1) | sandbox (incr 2)
    # v2 agentic caps (plan/15 #1; all overridable via mise env — see config.load_config)
    max_iters: int = 5  # per-peer research/deliberate loop iterations (per call)
    max_tool_calls: int = 8  # per-peer tool calls (per call)
    max_turns: int = 4  # deliberation free-form sub-rounds
    max_rounds: int = 4  # full ideation rounds
    max_msgs_per_peer: int = 3  # deliberation messages per peer per round (incl. opening; plan/23)


class Event(BaseModel):
    """One line in the run trace. Also the observability + eval datum."""

    run_id: str
    ts: str
    phase: str
    round: int = 0
    author_vendor: str | None = None
    kind: str
    payload: dict = Field(default_factory=dict)
