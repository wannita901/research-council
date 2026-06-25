"""Stage B/C reviewer-feedback formatters — the council's findings must surface in the
stream (like Stage A critiques), not just a finding count."""

from __future__ import annotations

from research_council.cli_ui import format_change_request, format_review_finding


def test_review_finding_shows_msg_fix_and_blocking_mark():
    out = format_review_finding(
        {
            "kind": "soundness",
            "severity": "high",
            "msg": "bakes the hypothesis",
            "fix": "use neutral data",
        }
    )
    assert "bakes the hypothesis" in out  # the actual feedback, not just a count
    assert "use neutral data" in out  # the suggested fix is shown too
    assert "(blocking)" in out  # high correctness/soundness is flagged as blocking
    assert "soundness" in out


def test_review_finding_non_blocking_has_no_mark():
    out = format_review_finding({"kind": "style", "severity": "low", "msg": "rename var"})
    assert "rename var" in out and "(blocking)" not in out


def test_change_request_shows_section_and_msg():
    out = format_change_request(
        {"section": "Results", "severity": "high", "msg": "unbacked 0.81 claim"}
    )
    assert "unbacked 0.81 claim" in out and "Results" in out and "high" in out


def test_change_request_defaults_section_to_whole():
    out = format_change_request({"section": "", "severity": "low", "msg": "tighten abstract"})
    assert "(whole)" in out and "tighten abstract" in out
