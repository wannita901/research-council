"""Writer + reviewer agents (Stage C council loop, plan/18).

The lead Writer drafts the whole paper, then revises only the sections the PC's
change-requests touch, with a final coherence pass. The PaperReviewer scores against the
venue rubric and files section-tagged change-requests. Offline-testable via TestModel.
"""

from __future__ import annotations

from pydantic_ai import Agent

from research_council import prompts
from research_council.obs.telemetry import UsageMeter, usage_of
from research_council.store.models import Citation, PaperDraft, ReviewNotes, VenueChoice


def _cost_add(meter: UsageMeter, result, price_model: str | None) -> None:
    u = usage_of(result)
    if u is None:
        return
    from research_council.providers.sdk import _cost

    it, ot = u.input_tokens or 0, u.output_tokens or 0
    meter.add(
        requests=u.requests or 0,
        input_tokens=it,
        output_tokens=ot,
        cost_usd=_cost(price_model, it, ot) if price_model else 0.0,
    )


def _refs_block(citations: list[Citation]) -> str:
    if not citations:
        return "Prior-art references you may cite: (none available — do not invent any)."
    lines = [f"- [{c.key}] {c.text}" for c in citations]
    return (
        "Prior-art references you MAY cite (use the exact key; cite none if irrelevant):\n"
        + "\n".join(lines)
    )


def _experiment_block(experiment: dict) -> str:
    rqs = experiment.get("rqs")
    if rqs:  # RQ-driven results (plan/21): one row per research question
        lines = ["Experiment results, per research question:"]
        for rr in rqs:
            res = rr.get("result", {})
            lines.append(
                f"- {rr.get('rq_id', '')}: {rr.get('question', '')} → "
                f"feasible={res.get('feasible')} approved={res.get('approved')} "
                f"metric={res.get('metric') or '—'}"
            )
        lines.append("Report results per RQ; only claim what each experiment actually showed.")
        return "\n".join(lines) + "\n"
    return (
        f"Experiment result: ran={experiment.get('ran')} feasible={experiment.get('feasible')} "
        f"metric={experiment.get('metric')}\nLog tail: {(experiment.get('log') or '')[:400]}\n"
    )


class Writer:
    def __init__(self, model, *, venue: str = "a CS venue", price_model: str | None = None):
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(
            model, output_type=PaperDraft, system_prompt=prompts.load("writing/writer", venue=venue)
        )

    async def draft(
        self,
        idea: dict,
        experiment: dict,
        constraints: dict | None = None,
        *,
        allowed_citations: list[Citation] | None = None,
        figure: str = "",
    ) -> PaperDraft:
        prompt = (
            f"Proposal: {idea.get('title', '')}\n"
            f"Problem statement: {idea.get('problem_statement', '') or idea.get('gap', '')}\n"
            f"Motivation: {idea.get('motivation', '')}\n"
            f"Hypothesis: {idea.get('hypothesis', '')}\n"
            f"Method: {idea.get('method', '')}\n"
            f"Experiment plan: {idea.get('experiment_plan', '')}\n"
            f"Dataset/metrics: {idea.get('dataset_metrics', '')}\n\n"
            f"{_experiment_block(experiment)}\n{_refs_block(allowed_citations or [])}\n"
            "Write the paper to reflect BOTH the proposal and the actual experiment result.\n"
        )
        if figure:
            prompt += (
                f"\nA results figure has been generated at: {figure} — reference it in Results.\n"
            )
        if constraints:
            prompt += "\nConstraints:\n" + "\n".join(f"- {k}: {v}" for k, v in constraints.items())
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        out = r.output
        # enforce grounding: drop any citation key the writer wasn't handed
        allowed = {c.key for c in (allowed_citations or [])}
        out.citations = [c for c in out.citations if c.key in allowed] if allowed else []
        if figure and not out.figure:
            out.figure = figure
        return out

    async def revise(self, draft: PaperDraft, change_requests, sections: list[str]) -> PaperDraft:
        """Rewrite only `sections`; returns a draft from which the engine merges those sections."""
        body = "\n\n".join(f"## {n}\n{draft.sections.get(n, '')}" for n in sections)
        reqs = "\n".join(
            f"- [{c.severity}] ({c.section or 'whole'}) {c.msg}" for c in change_requests
        )
        prompt = (
            f"Current title: {draft.title}\nCurrent abstract: {draft.abstract}\n\n"
            f"Revise ONLY these sections to address the change-requests; keep the paper's voice.\n\n"
            f"Sections to revise:\n{body[:4000]}\n\nChange-requests:\n{reqs}\n\n"
            "Return the full paper object, but only your edits to the listed sections (and the "
            "abstract if requested) will be kept."
        )
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        return r.output

    async def coherence_pass(self, draft: PaperDraft) -> PaperDraft:
        body = "\n\n".join(f"## {n}\n{b}" for n, b in draft.sections.items())
        prompt = (
            f"Final coherence pass. Title: {draft.title}\nAbstract: {draft.abstract}\n\n{body[:5000]}\n\n"
            "Smooth transitions and remove contradictions/repetition introduced by section-level "
            "edits. Do NOT add new claims or citations. Return the full polished paper."
        )
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        out = r.output
        out.citations = draft.citations  # never introduce citations in the coherence pass
        out.figure = draft.figure
        return out


class PaperReviewer:
    def __init__(
        self, model, *, venue: str = "a CS venue", vendor: str = "", price_model: str | None = None
    ):
        self.vendor = vendor
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(
            model,
            output_type=ReviewNotes,
            system_prompt=prompts.load("writing/reviewer", venue=venue),
        )

    async def review(self, draft: PaperDraft, rubric: dict) -> ReviewNotes:
        crit = "\n".join(f"- {k}: {v}" for k, v in rubric.items())
        body = "\n\n".join(f"## {n}\n{b}" for n, b in draft.sections.items())
        cites = ", ".join(c.key for c in draft.citations) or "(none)"
        prompt = (
            f"Rubric criteria:\n{crit}\n\nPaper:\nTitle: {draft.title}\nAbstract: {draft.abstract}\n"
            f"Citations: {cites}\n\n{body[:4000]}"
        )
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        out = r.output
        out.reviewer_vendor = self.vendor
        return out


class VenueRecommender:
    """One-shot: given the idea + result, recommend the best-fit venue from the catalog."""

    def __init__(self, model, *, price_model: str | None = None):
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(
            model, output_type=VenueChoice, system_prompt=prompts.load("writing/venue_recommender")
        )

    async def recommend(self, idea: dict, experiment: dict, venues: list[str]) -> VenueChoice:
        prompt = (
            f"Available venues: {', '.join(venues)}\n\n"
            f"Idea: {idea.get('title', '')}\nHypothesis: {idea.get('hypothesis', '')}\n"
            f"Method: {idea.get('method', '')}\n{_experiment_block(experiment)}\n"
            "Pick the single best-fit `venue` (must be one of the available ids) and give a "
            "one-line `rationale`."
        )
        r = await self._agent.run(prompt)
        _cost_add(self.usage, r, self._price_model)
        out = r.output
        if out.venue not in venues:
            out.venue = "generic"
        return out
