"""Tests for the pre-publication fact-check gate (pure logic, no network)."""
import json

from src.agents.fact_check_agent import (
    FACT_CHECK_MODEL_FALLBACKS,
    GEMMA_FACT_CHECK_FALLBACKS,
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


def test_quota_error_walks_models_then_raises(monkeypatch):
    """With a single exhausted key, a 429 walks all candidate models, then fails closed."""
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

    with pytest.raises(Exception, match="all fact-check models/keys failed"):
        agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    # primary + distinct gemini fallbacks + gemma fallbacks (one key)
    assert len(calls) == 1 + len(FACT_CHECK_MODEL_FALLBACKS) - 1 + len(GEMMA_FACT_CHECK_FALLBACKS)


def test_quota_error_rotates_keys(monkeypatch):
    """A 429 on the first key retries the same model with the next key."""
    agent = FactCheckAgent()
    calls = []

    class FakeResponse:
        text = '{"claims": []}'

    class FakeModels:
        def __init__(self, key):
            self.key = key

        def generate_content(self, model=None, contents=None, config=None):
            calls.append((self.key, model))
            if self.key == "key1":
                raise Exception("429 RESOURCE_EXHAUSTED")
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels(api_key)

    monkeypatch.setenv("LLM_API_KEYS", "key1,key2")
    monkeypatch.delenv("GEMINI_SCRIPT_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from google import genai
    monkeypatch.setattr(genai, "Client", FakeClient)

    raw = agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert raw == '{"claims": []}'
    assert calls[0][0] == "key1" and calls[1][0] == "key2"
    assert calls[0][1] == calls[1][1]  # same model, rotated key


def test_auth_error_raises_immediately(monkeypatch):
    """A non-quota, non-availability error raises at once - no masking."""
    import pytest
    agent = FactCheckAgent()
    calls = []

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            calls.append(model)
            raise Exception("403 PERMISSION_DENIED")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    with pytest.raises(Exception, match="403"):
        agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert len(calls) == 1


def test_gemma_ungrounded_fallback(monkeypatch):
    """Gemini models 404; Gemma rejects the search tool, then succeeds ungrounded."""
    agent = FactCheckAgent()
    calls = []

    class FakeResponse:
        text = '{"claims": []}'

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            has_tools = bool(getattr(config, "tools", None))
            calls.append((model, has_tools))
            if model.startswith("gemini-"):
                raise Exception("404 NOT_FOUND: model no longer available")
            if has_tools:
                raise Exception("400 INVALID_ARGUMENT: Tool use is not supported for this model")
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    raw = agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert raw == '{"claims": []}'
    gemma_calls = [c for c in calls if c[0].startswith("gemma-")]
    assert gemma_calls[0][1] is True   # grounded attempt first
    assert gemma_calls[1][1] is False  # ungrounded retry
    assert gemma_calls[1][0] == GEMMA_FACT_CHECK_FALLBACKS[0]


def test_gemma_error_fails_closed(monkeypatch):
    """A hard error from the Gemma stage halts the gate - no silent pass."""
    import pytest
    agent = FactCheckAgent()

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            if model.startswith("gemini-"):
                raise Exception("404 NOT_FOUND: model no longer available")
            if bool(getattr(config, "tools", None)):
                raise Exception("400 INVALID_ARGUMENT: Tool use is not supported for this model")
            raise Exception("500 INTERNAL")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    with pytest.raises(Exception, match="500"):
        agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")


def test_transient_503_walks_models(monkeypatch):
    """A 503 UNAVAILABLE demand spike retries in place, then rotates models."""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    agent = FactCheckAgent()
    calls = []

    class FakeResponse:
        text = '{"claims": []}'

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            calls.append(model)
            if len(calls) <= 2:
                raise Exception("503 UNAVAILABLE. This model is currently experiencing high demand.")
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    raw = agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert raw == '{"claims": []}'
    assert len(calls) == 3
    assert calls[0] == calls[1]  # in-place retry on the same model
    assert calls[1] != calls[2]  # then rotation to the next model


def test_empty_response_retries_once_then_walks_models(monkeypatch):
    """An empty response retries the same model once, then moves on."""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    agent = FactCheckAgent()
    calls = []

    class FakeResponse:
        text = '{"claims": []}'

    class FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            calls.append(model)
            if len(calls) <= 2:
                r = FakeResponse()
                r.text = ""
                return r
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda api_key=None: FakeClient())

    raw = agent._verify_with_grounded_model("Your SIP is losing 2% yearly", "t")
    assert raw == '{"claims": []}'
    assert len(calls) == 3
    assert calls[0] == calls[1]  # in-place retry after empty response
    assert calls[1] != calls[2]  # then rotation


def test_unparseable_response_retries_once(monkeypatch):
    """A truncated verdict JSON triggers one regeneration before failing closed."""
    import pytest
    agent = FactCheckAgent()
    calls = []

    def fake_verify(narration, thesis):
        calls.append(1)
        return '{"claims": [{"claim": "x", "verdict": "UNVERIF'

    monkeypatch.setattr(agent, "_verify_with_grounded_model", fake_verify)
    result = agent.check_script({"scenes": [{"narration": "prices are rising fast"}]}, thesis="t")
    assert len(calls) == 2
    assert result.passed is False
    assert result.check_ran is False
    assert "unparseable" in result.error


def test_unparseable_then_valid_recovers(monkeypatch):
    """If the retry returns clean JSON, the gate evaluates normally."""
    agent = FactCheckAgent()
    outputs = iter([
        '{"claims": [{"claim": "x", "verdict": "UNVERIF',
        '{"claims": [{"claim": "x", "verdict": "SUPPORTED", "reason": "ok"}]}',
    ])
    monkeypatch.setattr(agent, "_verify_with_grounded_model", lambda n, t: next(outputs))
    result = agent.check_script({"scenes": [{"narration": "prices are rising fast"}]}, thesis="t")
    assert result.check_ran is True
    assert result.passed is True


def test_groq_grounded_candidate_runs_first_when_key_set(monkeypatch):
    """With GROQ_API_KEY configured, the gate tries grounded Groq before Gemini."""
    import groq as groq_mod
    from src.utils.config import settings
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-groq-key")
    agent = FactCheckAgent()
    calls = []

    class FakeMsg:
        content = '{"claims": [{"claim": "x", "verdict": "SUPPORTED", "reason": "ok"}]}'

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeGroq:
        chat = FakeChat()

    monkeypatch.setattr(groq_mod, "Groq", lambda api_key=None: FakeGroq())

    raw = agent._verify_with_grounded_model("Prices are rising", "t")
    assert "SUPPORTED" in raw
    assert calls and calls[0]["model"] == "openai/gpt-oss-120b"
    assert calls[0]["tools"] == [{"type": "browser_search"}]


def test_parse_tolerates_prose_around_json():
    from src.agents.fact_check_agent import parse_verdict_response
    raw = 'Based on sources 【3L10-L12】, here is the verdict: {"claims": [{"claim": "c", "verdict": "SUPPORTED", "reason": "r"}]} - end.'
    claims = parse_verdict_response(raw)
    assert claims and claims[0]["verdict"] == "SUPPORTED"
