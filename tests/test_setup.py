"""Round-start setup helpers — model-choice parsing + connectable tool list."""

from __future__ import annotations

from research_council.cli import _parse_model_choices
from research_council.retrieval.registry import real_tools


def test_parse_model_choices_from_mise_comments():
    text = (
        'RC_OPENAI_MODEL = "gpt-5.4" # [ gpt-5.5 | gpt-5.4 | gpt-5.4-mini ]\n'
        'RC_ANTHROPIC_MODEL = "claude-sonnet-4-6" # [ claude-opus-4-8 | claude-sonnet-4-6 ]\n'
        'RC_GEMINI_MODEL = "gemini-3.5-flash" # [ gemini-3.1-pro-preview | gemini-3.5-flash ]\n'
    )
    d = _parse_model_choices(text)
    assert d["openai"] == ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]
    assert d["anthropic"] == ["claude-opus-4-8", "claude-sonnet-4-6"]
    assert d["gemini"][0] == "gemini-3.1-pro-preview"


def test_parse_model_choices_ignores_uncommented():
    assert _parse_model_choices('RC_OPENAI_MODEL = "gpt-5.4"\n') == {}


def test_real_tools_are_connectable_only():
    tools = real_tools()
    assert "web" not in tools  # stub, not connectable
    assert "paperswithcode" not in tools  # removed (anti-bot gated)
    assert {"wiki", "openalex", "arxiv", "semanticscholar", "github"} <= set(tools)
