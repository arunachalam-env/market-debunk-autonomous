"""
src/agents/fact_check_agent.py

Pre-publication fact-check gate.

A "debunk" channel cannot publish unverified claims. This agent runs after
script generation (before any TTS / visual spend) and:

1. Sends the full narration to a search-grounded Gemini model.
2. Asks it to extract every checkable factual claim (numbers, laws, dates,
   company or regulator actions) and verify each against current web sources.
3. Blocks publication when any claim is REFUTED or UNVERIFIABLE.

Fail-closed by design: if the check itself cannot run (no key, API error),
the run halts rather than publishing unchecked claims. Set
FACT_CHECK_REQUIRED=false in secrets to make a check failure non-blocking
(warn only), or FACT_CHECK_ENABLED=false to skip the gate entirely.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="fact_check")

_BLOCKING_VERDICTS = {"REFUTED", "UNVERIFIABLE"}

# gemini-2.5-flash was retired by Google (404 NOT_FOUND for new usage). Try the
# configured model first, then walk this list on model-not-found errors.
# gemini-3.1-flash-lite is the last resort: weaker, but has separate free-tier quota.
FACT_CHECK_MODEL_FALLBACKS = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite")
# Gemma is the last resort: separate free-tier quota pool from the Gemini flash
# models, but no Google-Search grounding - if the API rejects the search tool,
# the check runs on model knowledge only and the normal blocking rules still
# apply (UNVERIFIABLE blocks). If Gemma errors out, the gate fails closed.
GEMMA_FACT_CHECK_FALLBACKS = ("gemma-4-31b-it", "gemma-4-26b-a4b-it")

# Backoff before retrying a transient error or empty response on the same model/key.
_TRANSIENT_BACKOFF_S = 15

_UNGROUNDED_SUFFIX = (
    "\n\nNOTE: live web search is unavailable for this check. Verify each claim "
    "from your own knowledge only. If you cannot confidently confirm a claim "
    "from reliable knowledge, mark it UNVERIFIABLE."
)


def _tool_unsupported(msg: str) -> bool:
    m = msg.lower()
    return "invalid_argument" in m or ("tool" in m and "not supported" in m)


@dataclass
class FactCheckResult:
    """Outcome of the pre-publication fact-check gate."""

    passed: bool
    claims: list[dict] = field(default_factory=list)
    blocking_claims: list[dict] = field(default_factory=list)
    check_ran: bool = True
    error: Optional[str] = None

    def summary(self) -> str:
        if not self.check_ran:
            return f"fact-check could not run ({self.error})"
        if self.passed:
            return f"all {len(self.claims)} claim(s) supported by current sources"
        lines = [f"{len(self.blocking_claims)} of {len(self.claims)} claim(s) failed verification:"]
        for c in self.blocking_claims:
            lines.append(f"  - [{c.get('verdict')}] {c.get('claim')}: {c.get('reason', '')}")
        return "\n".join(lines)


def parse_verdict_response(raw_text: str) -> list[dict]:
    """Parse the model's JSON verdict list. Tolerates markdown fences."""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("claims", [])
    if not isinstance(data, list):
        return []
    claims = []
    for item in data:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict", "")).strip().upper()
        if verdict not in ("SUPPORTED", "REFUTED", "UNVERIFIABLE"):
            verdict = "UNVERIFIABLE"
        claims.append(
            {
                "claim": str(item.get("claim", "")).strip(),
                "verdict": verdict,
                "reason": str(item.get("reason", "")).strip(),
            }
        )
    return [c for c in claims if c["claim"]]


def evaluate_claims(claims: list[dict]) -> FactCheckResult:
    """Pure decision logic: block when any claim is refuted or unverifiable."""
    blocking = [c for c in claims if c.get("verdict") in _BLOCKING_VERDICTS]
    return FactCheckResult(passed=not blocking, claims=claims, blocking_claims=blocking)


class FactCheckAgent:
    """Verifies narration claims with a search-grounded model before publishing."""

    def __init__(self, model: Optional[str] = None):
        configured = model or getattr(settings, "FACT_CHECK_MODEL", "")
        self.model = configured or FACT_CHECK_MODEL_FALLBACKS[0]

    def check_script(self, script_dict: dict, thesis: str = "") -> FactCheckResult:
        """Run the gate. Returns a FactCheckResult; caller decides what blocking means."""
        narration = "\n".join(
            str(scene.get("narration", "")).strip()
            for scene in script_dict.get("scenes", [])
            if scene.get("narration")
        )
        if not narration.strip():
            return FactCheckResult(passed=False, check_ran=False, error="empty narration")

        try:
            raw = self._verify_with_grounded_model(narration, thesis)
        except Exception as exc:
            log.error("Fact-check call failed: %s", exc)
            return FactCheckResult(passed=False, check_ran=False, error=str(exc))

        try:
            claims = parse_verdict_response(raw)
        except Exception as exc:
            log.error("Fact-check response unparseable: %s", exc)
            return FactCheckResult(passed=False, check_ran=False, error=f"unparseable response: {exc}")

        if not claims:
            log.warning("Fact-check found no checkable claims; treating as unverified content.")
            return FactCheckResult(passed=False, check_ran=True, claims=[], blocking_claims=[], error="no checkable claims found")

        result = evaluate_claims(claims)
        if result.passed:
            log.info("✅ Fact-check passed: %s", result.summary())
        else:
            log.warning("🛑 Fact-check BLOCKED publication:\n%s", result.summary())
        return result

    def _verify_with_grounded_model(self, narration: str, thesis: str) -> str:
        from google import genai
        from google.genai import types

        keys_str = (
            os.getenv("GEMINI_SCRIPT_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("LLM_API_KEYS")
            or getattr(settings, "GEMINI_SCRIPT_API_KEY", "")
            or getattr(settings, "GEMINI_API_KEY", "")
            or getattr(settings, "LLM_API_KEYS", "")
        )
        api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        if not api_keys:
            raise RuntimeError("no Gemini API key available for fact-checking")
        prompt = f"""You are a meticulous financial fact-checker for an Indian retail-investor Shorts channel.

Below is the full narration of a 25-second video (topic thesis: {thesis or 'n/a'}).

NARRATION:
{narration}

Task:
1. Extract every checkable factual claim (statistics, percentages, rupee amounts, laws/regulations, regulator actions, company events, dates). Skip pure opinion, generic advice, and rhetorical hooks.
2. Verify each claim against current, reputable web sources using Google Search.
3. Verdicts: SUPPORTED (a reputable current source confirms it), REFUTED (a reputable current source contradicts it), UNVERIFIABLE (no reliable source found, or the claim is exaggerated/misleadingly framed).

Be strict: this channel debunks myths, so its own claims must be airtight. When in doubt, mark UNVERIFIABLE.

Return strict JSON only:
{{"claims": [{{"claim": "...", "verdict": "SUPPORTED|REFUTED|UNVERIFIABLE", "reason": "one short sentence with the source basis"}}]}}"""

        candidates = [self.model] + [m for m in FACT_CHECK_MODEL_FALLBACKS if m != self.model]
        candidates += [m for m in GEMMA_FACT_CHECK_FALLBACKS if m not in candidates]
        last_exc: Optional[Exception] = None
        for candidate in candidates:
            for key_index, api_key in enumerate(api_keys):
                client = genai.Client(api_key=api_key)
                for attempt in (1, 2):
                    try:
                        text, grounded = self._generate_checked(client, candidate, prompt, types)
                        if text:
                            if candidate != self.model or key_index > 0:
                                log.warning(
                                    "Fact-check succeeded with fallback (model %s, key #%d).",
                                    candidate, key_index + 1,
                                )
                            if not grounded:
                                log.warning(
                                    "Fact-check ran UNGROUNDED on %s: verdicts are model-knowledge only (no live search).",
                                    candidate,
                                )
                            return text
                        last_exc = RuntimeError("empty response from fact-check model")
                        if attempt == 1:
                            log.warning(
                                "Fact-check empty response from %s (key #%d); retrying once after %ds backoff.",
                                candidate, key_index + 1, _TRANSIENT_BACKOFF_S,
                            )
                            time.sleep(_TRANSIENT_BACKOFF_S)
                            continue  # retry same model/key once
                        break  # empty twice: next key/model
                    except Exception as exc:
                        last_exc = exc
                        msg = str(exc)
                        if "NOT_FOUND" in msg or "no longer available" in msg:
                            log.warning("Fact-check model %s not available; trying next model.", candidate)
                            break  # next key/model
                        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                            if key_index + 1 < len(api_keys):
                                log.warning("Fact-check quota exhausted on key #%d for %s; rotating key.", key_index + 1, candidate)
                            else:
                                log.warning("Fact-check quota exhausted on all keys for %s; trying next model.", candidate)
                            break  # next key/model
                        # Transient backend errors (demand spikes, timeouts):
                        # retry the same model/key once after a short backoff,
                        # then rotate like quota exhaustion. Still fail-closed
                        # if every candidate errors out.
                        if (
                            "503" in msg
                            or "UNAVAILABLE" in msg
                            or "DEADLINE_EXCEEDED" in msg
                            or "high demand" in msg.lower()
                        ):
                            if attempt == 1:
                                log.warning(
                                    "Fact-check transient error on %s (key #%d): %s; retrying once after %ds backoff.",
                                    candidate, key_index + 1, msg.splitlines()[0][:120], _TRANSIENT_BACKOFF_S,
                                )
                                time.sleep(_TRANSIENT_BACKOFF_S)
                                continue  # retry same model/key once
                            log.warning(
                                "Fact-check transient error persists on %s (key #%d): %s; trying next key/model.",
                                candidate, key_index + 1, msg.splitlines()[0][:120],
                            )
                            break  # next key/model
                        raise
        raise RuntimeError(f"all fact-check models/keys failed: {last_exc}")

    @staticmethod
    def _generate_checked(client, candidate: str, prompt: str, types):
        """One model attempt. Runs search-grounded; for Gemma models (no search
        tool support) retries once without the tool. Returns (text, grounded)."""
        def _call(grounded: bool, text: str):
            kwargs = dict(temperature=0.1, max_output_tokens=1500)
            if grounded:
                kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            return client.models.generate_content(
                model=candidate,
                contents=text,
                config=types.GenerateContentConfig(**kwargs),
            )

        try:
            response = _call(True, prompt)
            return (response.text if response else None), True
        except Exception as exc:
            if candidate.startswith("gemma-") and _tool_unsupported(str(exc)):
                log.warning("Model %s rejects search grounding; retrying ungrounded.", candidate)
                response = _call(False, prompt + _UNGROUNDED_SUFFIX)
                return (response.text if response else None), False
            raise
