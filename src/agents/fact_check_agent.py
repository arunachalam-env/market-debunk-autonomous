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
from dataclasses import dataclass, field
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="fact_check")

_BLOCKING_VERDICTS = {"REFUTED", "UNVERIFIABLE"}


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
        self.model = model or getattr(settings, "FACT_CHECK_MODEL", "") or "gemini-2.5-flash"

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

        api_key = (
            os.getenv("GEMINI_SCRIPT_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or getattr(settings, "GEMINI_SCRIPT_API_KEY", "")
            or getattr(settings, "GEMINI_API_KEY", "")
        )
        if not api_key:
            raise RuntimeError("no Gemini API key available for fact-checking")

        client = genai.Client(api_key=api_key)
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

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=1500,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        if not response or not response.text:
            raise RuntimeError("empty response from fact-check model")
        return response.text
