"""Unified tool interface (plan/15 Tier-1 #2). Agents call Tools; the agent lib
(PydanticAI) is adapted to these, so tools stay swappable."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    content: str  # what the agent reads back
    refs: list[str] = Field(default_factory=list)  # source ids touched


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str

    async def run(self, **kwargs) -> ToolResult:
        ...
