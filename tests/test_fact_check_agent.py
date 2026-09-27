"""Tests for the pre-publication fact-check gate (pure logic, no network)."""
import json

from src.agents.fact_check_agent import (
    FactCheckAgent,
    FactCheckResult,
    evaluate_claims,
    parse_verdict_response,
)


def test_parse_strict_json():
    raw = json.dumps({"claims": [
        {"claim": "RBI raised repo rate to 9%", "verdict": "REFUTED", "reason": "RBI site shows 6.5%"},
        {"claim": "SEBI regulates mutual funds", "verdict": "SUPPORTED", "reason": "sebi.gov.in"},
    ]})
    claims = parse_verdict_response(raw)
    assert len(claims) == 2
    assert claims[0]["verdict"] == "REFUTED"


def test_parse_strips_markdown_fences():
    raw = '```json\n{"claims": [{"claim": "X", "verdict": "supported", "reason": "y"}]}\n```'
    claims = parse_verdict_response(raw)
    assert claims[0]["verdict"] == "SUPPORTED"


def test_unknown_verdict_becomes_unverifiable():
    raw = json.dumps([{"claim": "Something vague", "verdict": "MAYBE", "reason": ""}])
    claims = parse_verdict_response(raw)
    assert claims[0]["verdict"] == "UNVERIFIABLE"


def test_refuted_claim_blocks():
    result = evaluate_claims([
        {"claim": "a", "verdict": "SUPPORTED", "reason": ""},
        {"claim": "b", "verdict": "REFUTED", "reason": "contradicted"},
    ])
    assert result.passed is False
    assert len(result.blocking_claims) == 1


def test_unverifiable_claim_blocks():
    result = evaluate_claims([{"claim": "a", "verdict": "UNVERIFIABLE", "reason": "no source"}])
    assert result.passed is False


def test_all_supported_passes():
    result = evaluate_claims([
        {"claim": "a", "verdict": "SUPPORTED", "reason": ""},
        {"claim": "b", "verdict": "SUPPORTED", "reason": ""},
    ])
    assert result.passed is True
    assert result.blocking_claims == []


def test_empty_narration_fails_closed():
    agent = FactCheckAgent()
    result = agent.check_script({"scenes": []})
    assert result.passed is False
    assert result.check_ran is False


def test_api_error_fails_closed(monkeypatch):
    agent = FactCheckAgent()
    def boom(_narration, _thesis):
        raise RuntimeError("no key")
    monkeypatch.setattr(agent, "_verify_with_grounded_model", boom)
    result = agent.check_script({"scenes": [{"narration": "Banks charge 40% hidden fees."}]})
    assert result.passed is False
    assert result.check_ran is False
    assert "no key" in result.error


def test_summary_lists_blocking_claims():
    result = evaluate_claims([{"claim": "Movie tickets have 570% tax", "verdict": "REFUTED", "reason": "GST is 18-28%"}])
    text = result.summary()
    assert "570%" in text and "REFUTED" in text


def test_model_fallback_on_not_found(monkeypatch):
    """Retired primary model (404 NOT_FOUND) falls through to the next model."""
    agent = FactCheckAgent()
    calls = []

    class FakeResponse:
        text = '{"claims": []}'

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            calls.append(model)
            if len(calls) == 1:
                raise Exception("404 NOT_FOUND. This model is no longer available to new users.")
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    raw = agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert raw == '{"claims": []}'
    assert len(calls) == 2
    assert calls[0] != calls[1]


def test_non_model_error_does_not_fallback(monkeypatch):
    """A quota/auth error raises immediately instead of walking the list."""
    import pytest
    agent = FactCheckAgent()
    calls = []

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            calls.append(model)
            raise Exception("429 RESOURCE_EXHAUSTED")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    with pytest.raises(Exception, match="429"):
        agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert len(calls) == 1
