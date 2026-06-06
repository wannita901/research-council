"""Agentic peer (v2, plan/12 + plan/15 Tier-1) — a PydanticAI agent with tools + caps.

In the research phase, each peer independently drives a `think → call tool → observe`
loop (search / verify_claim), bounded by UsageLimits, and returns a structured brief.
Pass a real model string for --live, or a TestModel for offline tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import UsageLimits


def _drop_pending_tool_calls(msgs: list) -> list:
    """When a cap is hit, the partial history can end with a model turn that requested tools
    which were never executed. PydanticAI rejects a fresh prompt on top of dangling tool
    calls, so trim trailing model responses that contain any tool-call part — keeping all the
    earlier, already-answered observations."""
    out = list(msgs)
    while out and isinstance(out[-1], ModelResponse) and any(
        isinstance(p, ToolCallPart) for p in out[-1].parts
    ):
        out.pop()
    return out

from research_council.debate.deliberation import render_view
from research_council.retrieval.base import RetrievalProvider
from research_council.store.models import (
    BriefDraft,
    Candidate,
    CandidateDraft,
    Contribution,
    DiscussionMessage,
    ResearchBrief,
    Score,
    ScoreSheet,
)
from research_council.tools.search import SearchTool
from research_council.tools.verify import VerifyTool

# PydanticAI model-name prefixes per vendor seat. `openai-chat` keeps Chat Completions
# (plain `openai:` defaults to the Responses API in v2); `google` replaces the deprecated
# `google-gla`. Both silence the v1 deprecation warnings.
_PREFIX = {"openai": "openai-chat", "anthropic": "anthropic", "gemini": "google"}


def agent_model_name(vendor: str, model: str) -> str:
    """e.g. ('gemini','gemini-3.5-flash') -> 'google:gemini-3.5-flash'."""
    return f"{_PREFIX.get(vendor, vendor)}:{model}"


RESEARCH_SYS = (
    "You are {codename}, an AI4SE research scientist on a council. Independently survey the "
    "literature with the `search` tool and ground uncertain claims with `verify_claim`. "
    "Identify ONE specific, underexplored research gap. Cite sources by their id in `refs`; "
    "never invent citations. Be rigorous and concrete."
)

PROPOSE_SYS = (
    "You are {codename}. Turn your research gap into a concrete, testable idea AND a MINIMAL "
    "experiment plan (name dataset, baseline, method, metric, and the smallest runnable step). "
    "Be specific."
)

JUDGE_SYS = (
    "You are {codename}, scoring anonymized AI4SE research candidates (authorship hidden). "
    "Score each candidate 0..1 on novelty, soundness, feasibility, clarity, by its label. "
    "Be calibrated and fair."
)

DELIBERATE_SYS = (
    "You are {codename} on an AI4SE research council, in a group discussion. Read the candidates "
    "and the thread, then contribute ONE message: critique (set `targets` to a candidate id), ask "
    "a question (set `to` a codename), answer a question addressed to you, defend, concede, or "
    "revise. Use `verify_claim` to ground disputed claims. Be specific and brief. Set done=true "
    "only when you have nothing substantive to add."
)


@dataclass
class ResearchDeps:
    search_tool: SearchTool
    verify_tool: VerifyTool


async def search(ctx: RunContext[ResearchDeps], query: str) -> str:
    """Search the enabled literature/code sources for a query; returns results with ids."""
    return (await ctx.deps.search_tool.run(query=query)).content


async def verify_claim(ctx: RunContext[ResearchDeps], claim: str, kind: str = "existence") -> str:
    """Ground a claim by checking supporting sources exist (kind: citation|benchmark|repo|existence)."""
    return (await ctx.deps.verify_tool.run(claim=claim, kind=kind)).content


class AgentPeer:
    def __init__(self, vendor: str, codename: str, model, retrieval: RetrievalProvider,
                 *, max_iters: int = 5, max_tool_calls: int = 8, k: int = 8):
        self.vendor = vendor
        self.codename = codename
        self._deps = ResearchDeps(SearchTool(retrieval, k), VerifyTool(retrieval))
        self._limits = UsageLimits(request_limit=max_iters, tool_calls_limit=max_tool_calls)
        self._research_agent: Agent = Agent(
            model,
            output_type=BriefDraft,
            deps_type=ResearchDeps,
            system_prompt=RESEARCH_SYS.format(codename=codename),
            tools=[search, verify_claim],
        )
        self._delib_agent: Agent = Agent(
            model,
            output_type=Contribution,
            deps_type=ResearchDeps,
            system_prompt=DELIBERATE_SYS.format(codename=codename),
            tools=[search, verify_claim],
        )
        self._propose_agent: Agent = Agent(
            model, output_type=CandidateDraft, system_prompt=PROPOSE_SYS.format(codename=codename),
        )
        self._judge_agent: Agent = Agent(
            model, output_type=ScoreSheet, system_prompt=JUDGE_SYS.format(codename=codename),
        )
        # Tool-less twins used to finalize when the tool/iteration budget is hit mid-loop:
        # they coerce a structured result out of what was already gathered, no more tool calls.
        self._research_finalize: Agent = Agent(
            model, output_type=BriefDraft, system_prompt=RESEARCH_SYS.format(codename=codename),
        )
        self._delib_finalize: Agent = Agent(
            model, output_type=Contribution, system_prompt=DELIBERATE_SYS.format(codename=codename),
        )

    async def _capped(self, agent: Agent, finalizer: Agent, prompt: str):
        """Run an agentic (tool-using) agent under its caps. If a cap is hit before a final
        result, don't crash — finalize tool-lessly from the gathered context. The caps thus
        BOUND the loop instead of aborting the whole debate."""
        async with agent.iter(prompt, deps=self._deps, usage_limits=self._limits) as run:
            try:
                async for _ in run:
                    pass
            except UsageLimitExceeded:
                pass  # budget reached — fall through to finalize
            result = run.result
            try:
                msgs = _drop_pending_tool_calls(run.all_messages())
            except Exception:
                msgs = None
        if result is not None:
            return result.output
        kwargs = {"message_history": msgs} if msgs else {}
        fin = await finalizer.run(
            "You've reached your research budget. Based ONLY on what you've gathered so far, "
            "produce the required structured output now — do NOT call any tools.",
            **kwargs,
        )
        return fin.output

    async def research(self, topic: str, context: str = "") -> ResearchBrief:
        prompt = f"Topic: {topic}" + (f"\n\nPrior context:\n{context}" if context else "")
        d: BriefDraft = await self._capped(self._research_agent, self._research_finalize, prompt)
        return ResearchBrief(vendor=self.vendor, landscape=d.landscape, gap=d.gap,
                             rationale=d.rationale, refs=d.refs)

    async def deliberate(self, thread: list[DiscussionMessage], candidates: list[Candidate],
                         my_open_questions: list[str]) -> Contribution:
        prompt = render_view(thread, candidates, my_open_questions, self.codename)
        return await self._capped(self._delib_agent, self._delib_finalize, prompt)

    async def propose(self, brief: ResearchBrief, constraints_text: str = "") -> Candidate:
        prompt = f"Gap: {brief.gap}\nLandscape: {brief.landscape}"
        if constraints_text:
            prompt += f"\n\n{constraints_text}"
        d: CandidateDraft = (await self._propose_agent.run(prompt)).output
        return Candidate(id=self.codename, vendor=self.vendor, title=d.title, gap=brief.gap,
                         hypothesis=d.hypothesis, method=d.method, experiment_plan=d.experiment_plan,
                         refs=brief.refs)

    async def score(self, anon_candidates: list[dict], context: str = "") -> list[Score]:
        listing = "\n".join(
            f"{a['label']}: {a.get('title', '')} — {a.get('gap', '')}" for a in anon_candidates
        )
        sheet: ScoreSheet = (await self._judge_agent.run(f"Candidates:\n{listing}")).output
        return [Score(judge_vendor=self.vendor, candidate_id=i.label, novelty=i.novelty,
                      soundness=i.soundness, feasibility=i.feasibility, clarity=i.clarity)
                for i in sheet.items]
