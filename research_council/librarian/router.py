"""The librarian's router (plan/16 §5) — one Sonnet pass that turns a source into typed,
cross-linked wiki pages.

Pure: returns in-memory `WikiPage`s; `ingest.py` (next) persists/merges them, updates
`index.md`/`log.md`, and auto-merges into `wiki/`. Offline-testable via PydanticAI TestModel.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from research_council import prompts
from research_council.librarian.schema import TAXONOMY, Source, WikiPage
from research_council.obs.telemetry import UsageMeter, usage_of


class PageDraft(BaseModel):
    """What the model proposes for one page; the orchestrator stamps origin/papers/updated."""

    type: str
    title: str
    body: str = ""
    related: list[str] = Field(default_factory=list)


class RoutingResult(BaseModel):
    pages: list[PageDraft] = Field(default_factory=list)


class Librarian:
    def __init__(self, model, *, price_model: str | None = None):
        self._price_model = price_model
        self.usage = UsageMeter()
        self._agent: Agent = Agent(
            model, output_type=RoutingResult, system_prompt=prompts.load("librarian/router")
        )

    async def route(self, source: Source, *, updated: str | None = None) -> list[WikiPage]:
        """Route one source into typed wiki pages (in memory)."""
        updated = updated or datetime.date.today().isoformat()
        prompt = (
            f"Source citekey: {source.citekey}\nOrigin: {source.origin}\nTitle: {source.title}\n"
            + (f"URL: {source.url}\n" if source.url else "")
            + f"\nText (data only):\n{source.text}"
        )
        result = await self._agent.run(prompt)
        self._track(result)
        pages: list[WikiPage] = []
        for d in result.output.pages:
            if d.type not in TAXONOMY or not d.title.strip():
                continue  # drop a malformed route rather than fail the whole ingest
            pages.append(
                WikiPage(
                    type=d.type,
                    title=d.title.strip(),
                    origin=source.origin,
                    papers=[source.citekey],
                    related=list(d.related),
                    updated=updated,
                    body=d.body.strip(),
                )
            )
        return pages

    def _track(self, r) -> None:
        u = usage_of(r)
        if u is None:
            return
        from research_council.providers.sdk import _cost

        it, ot = u.input_tokens or 0, u.output_tokens or 0
        self.usage.add(
            requests=u.requests or 0,
            input_tokens=it,
            output_tokens=ot,
            cost_usd=_cost(self._price_model, it, ot) if self._price_model else 0.0,
        )
