"""Provider adapters construct + satisfy the Protocol offline (no API calls)."""

from __future__ import annotations

import pytest

from research_council.providers.base import LLMProvider

pytest.importorskip("openai")
pytest.importorskip("anthropic")
pytest.importorskip("google.genai")


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")


def test_build_each_provider_and_client():
    from research_council.providers.sdk import build_provider

    for vendor, model in [("openai", "gpt-5"), ("anthropic", "claude-opus-4-8"),
                          ("gemini", "gemini-2.5-pro")]:
        p = build_provider(vendor, model)
        assert isinstance(p, LLMProvider)
        assert p.name == vendor and p.model == model
        assert p.client is not None  # lazy SDK client constructs offline (no request)


def test_missing_key_raises(monkeypatch):
    from research_council.providers.sdk import build_provider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_provider("openai", "gpt-5")


def test_cost_table():
    from research_council.providers.sdk import _cost

    assert _cost("gpt-5", 1_000_000, 0) == 1.25
    assert _cost("unknown-model", 9_999, 9_999) == 0.0
