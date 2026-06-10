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

from research_council import prompts
from research_council.debate.deliberation import render_view
from research_council.obs.telemetry import UsageMeter, usage_of
from research_council.providers.sdk import _cost
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


def _extract_tool_calls(msgs: list) -> list[dict]:
    """Pull every tool call the agent made out of its message history, as
    {tool, args} records — so the trace shows what each peer actually searched/verified."""
    out: list[dict] = []
    for m in msgs:
        for part in getattr(m, "parts", []):
            if isinstance(part, ToolCallPart):
                try:
                    d = part.args_as_dict()
                    arg = d.get("query") or d.get("claim") or (str(d) if d else "")
                except Exception:
                    arg = str(getattr(part, "args", ""))
                out.append({"tool": getattr(part, "tool_name", "?"), "args": str(arg)[:200]})
    return out


def _drop_pending_tool_calls(msgs: list) -> list:
    """When a cap is hit, the partial history can end with a model turn that requested tools
    which were never executed. PydanticAI rejects a fresh prompt on top of dangling tool
    calls, so trim trailing model responses that contain any tool-call part — keeping all the
    earlier, already-answered observations."""
    out = list(msgs)
    while (
        out
        and isinstance(out[-1], ModelResponse)
        and any(isinstance(p, ToolCallPart) for p in out[-1].parts)
    ):
        out.pop()
    return out


# PydanticAI model-name prefixes per vendor seat. `openai-chat` keeps Chat Completions
# (plain `openai:` defaults to the Responses API in v2); `google` replaces the deprecated
# `google-gla`. Both silence the v1 deprecation warnings.
_PREFIX = {"openai": "openai-chat", "anthropic": "anthropic", "gemini": "google"}


def agent_model_name(vendor: str, model: str) -> str:
    """e.g. ('gemini','gemini-3.5-flash') -> 'google:gemini-3.5-flash'."""
    return f"{_PREFIX.get(vendor, vendor)}:{model}"


# System prompts live in research_council/prompts/peer/*.md (loaded below): research ·
# propose · judge · deliberate · finalize.


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
    def __init__(
        self,
        vendor: str,
        codename: str,
        model,
        retrieval: RetrievalProvider,
        *,
        max_iters: int = 5,
        max_tool_calls: int = 8,
        k: int = 8,
        price_model: str | None = None,
    ):
        self.vendor = vendor
        self.codename = codename
        self._price_model = price_model  # bare seat id (e.g. "gpt-5.4") for costing; None → free
        self.usage = UsageMeter()
        self.last_tool_calls: list[dict] = []  # tool calls of the most recent research/deliberate
        self._deps = ResearchDeps(SearchTool(retrieval, k), VerifyTool(retrieval))
        self._limits = UsageLimits(request_limit=max_iters, tool_calls_limit=max_tool_calls)
        self._research_agent: Agent = Agent(
            model,
            output_type=BriefDraft,
            deps_type=ResearchDeps,
            system_prompt=prompts.load("peer/research", codename=codename),
            tools=[search, verify_claim],
        )
        self._delib_agent: Agent = Agent(
            model,
            output_type=Contribution,
            deps_type=ResearchDeps,
            system_prompt=prompts.load("peer/deliberate", codename=codename),
            tools=[search, verify_claim],
        )
        self._propose_agent: Agent = Agent(
            model,
            output_type=CandidateDraft,
            system_prompt=prompts.load("peer/propose", codename=codename),
        )
        self._judge_agent: Agent = Agent(
            model,
            output_type=ScoreSheet,
            system_prompt=prompts.load("peer/judge", codename=codename),
        )
        # Tool-less twins used to finalize when the tool/iteration budget is hit mid-loop:
        # they coerce a structured result out of what was already gathered, no more tool calls.
        self._research_finalize: Agent = Agent(
            model,
            output_type=BriefDraft,
            system_prompt=prompts.load("peer/research", codename=codename),
        )
        self._delib_finalize: Agent = Agent(
            model,
            output_type=Contribution,
            system_prompt=prompts.load("peer/deliberate", codename=codename),
        )

    def _track(self, x) -> None:
        """Add one PydanticAI run's usage (tokens/calls + costed $) to this peer's tally."""
        u = usage_of(x)
        if u is None:
            return
        it = getattr(u, "input_tokens", 0) or 0
        ot = getattr(u, "output_tokens", 0) or 0
        self.usage.add(
            requests=getattr(u, "requests", 0) or 0,
            input_tokens=it,
            output_tokens=ot,
            tool_calls=getattr(u, "tool_calls", 0) or 0,
            cost_usd=_cost(self._price_model, it, ot) if self._price_model else 0.0,
        )

    async def _capped(
        self, agent: Agent, finalizer: Agent, prompt: str, finalize_prompt: str = "peer/finalize"
    ):
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
            self._track(run)
            try:
                all_msgs = run.all_messages()
                self.last_tool_calls = _extract_tool_calls(all_msgs)
                msgs = _drop_pending_tool_calls(all_msgs)
            except Exception:
                self.last_tool_calls, msgs = [], None
        if result is not None:
            return result.output
        kwargs = {"message_history": msgs} if msgs else {}
        fin = await finalizer.run(prompts.load(finalize_prompt), **kwargs)
        self._track(fin)
        return fin.output

    async def research(self, topic: str, context: str = "") -> ResearchBrief:
        prompt = f"Topic: {topic}" + (f"\n\nPrior context:\n{context}" if context else "")
        d: BriefDraft = await self._capped(self._research_agent, self._research_finalize, prompt)
        return ResearchBrief(
            vendor=self.vendor, landscape=d.landscape, gap=d.gap, rationale=d.rationale, refs=d.refs
        )

    async def deliberate(
        self,
        thread: list[DiscussionMessage],
        candidates: list[Candidate],
        my_open_questions: list[str],
        *,
        require_critique: bool = False,
    ) -> Contribution:
        prompt = render_view(thread, candidates, my_open_questions, self.codename)
        if require_critique:
            prompt += (
                "\n\nOPENING TURN: you MUST contribute a `critique` (or a `question`) "
                "addressed to a peer whose candidate is NOT your own. Do NOT pass, concede, "
                "or set done — name the peer's codename in `targets` and give a concrete concern."
            )
            return await self._capped(
                self._delib_agent,
                self._delib_finalize,
                prompt,
                finalize_prompt="peer/finalize_critique",
            )
        return await self._capped(self._delib_agent, self._delib_finalize, prompt)

    async def propose(self, brief: ResearchBrief, constraints_text: str = "") -> Candidate:
        prompt = f"Gap: {brief.gap}\nLandscape: {brief.landscape}"
        if constraints_text:
            prompt += f"\n\n{constraints_text}"
        r = await self._propose_agent.run(prompt)
        self._track(r)
        d: CandidateDraft = r.output
        return Candidate(
            id=self.codename,
            vendor=self.vendor,
            title=d.title,
            gap=brief.gap,
            hypothesis=d.hypothesis,
            method=d.method,
            experiment_plan=d.experiment_plan,
            problem_statement=d.problem_statement,
            motivation=d.motivation,
            research_questions=d.research_questions,
            dataset_metrics=d.dataset_metrics,
            fallback_plan=d.fallback_plan,
            refs=brief.refs,
        )

    async def score(self, anon_candidates: list[dict], context: str = "") -> list[Score]:
        def _fmt(a: dict) -> str:
            rqs = "; ".join(
                f"{q.get('id', '')}: {q.get('question', '')}"
                for q in a.get("research_questions", [])
            )
            return (
                f"### {a['label']}: {a.get('title', '')}\n"
                f"Problem: {a.get('problem_statement', '') or a.get('gap', '')}\n"
                f"Motivation: {a.get('motivation', '')}\n"
                f"Hypothesis: {a.get('hypothesis', '')}\n"
                f"Method: {a.get('method', '')}\n"
                f"Research questions: {rqs}\n"
                f"Experiment plan: {a.get('experiment_plan', '')}\n"
                f"Dataset/metrics: {a.get('dataset_metrics', '')}\n"
                f"Fallback: {a.get('fallback_plan', '')}"
            )

        listing = "\n\n".join(_fmt(a) for a in anon_candidates)
        r = await self._judge_agent.run(f"Proposals:\n{listing}")
        self._track(r)
        sheet: ScoreSheet = r.output
        return [
            Score(
                judge_vendor=self.vendor,
                candidate_id=i.label,
                novelty=i.novelty,
                soundness=i.soundness,
                feasibility=i.feasibility,
                clarity=i.clarity,
            )
            for i in sheet.items
        ]
