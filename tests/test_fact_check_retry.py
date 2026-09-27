"""Regression tests for the fact-check feedback loop.

When the pre-publication fact-check gate blocks a draft, the manager
regenerates the script once, passing the failed claims back to the writer
via ``forbidden_claims``. These tests pin the prompt-side contract.
"""

import pytest

from src.agents import script_agent


def _capture_prompts(monkeypatch):
    seen = []

    def fake_call_model(prompt, model):
        seen.append(prompt)
        raise ValueError("sentinel: stop after capturing the prompt")

    monkeypatch.setattr(script_agent, "_call_model", fake_call_model)
    return seen


def test_generate_script_feeds_forbidden_claims_to_model(monkeypatch):
    seen = _capture_prompts(monkeypatch)
    with pytest.raises(Exception):
        script_agent.generate_script(
            "Manufacturers are passing costs to consumers.",
            "Market Debunk",
            story_seed=None,
            forbidden_claims=["an 11-13% cumulative price hike onto consumers"],
        )
    assert seen, "model was never called"
    assert any(
        "FACT-CHECK FEEDBACK" in p and "11-13% cumulative price hike" in p
        for p in seen
    ), "forbidden claims must be injected into the regeneration prompt"


def test_generate_script_without_forbidden_claims_omits_feedback_block(monkeypatch):
    seen = _capture_prompts(monkeypatch)
    with pytest.raises(Exception):
        script_agent.generate_script(
            "Manufacturers are passing costs to consumers.",
            "Market Debunk",
            story_seed=None,
        )
    assert seen, "model was never called"
    assert all("FACT-CHECK FEEDBACK" not in p for p in seen)
