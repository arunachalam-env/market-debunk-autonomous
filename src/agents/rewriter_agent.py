"""
src/agents/rewriter_agent.py

English Script Rewriter Agent (The "Script Doctor")
----------------------------------------------------
Intercepts generated English video scripts that fail Evaluator gates or quality checks
(hook too long, citation language, duplicate visual prompts, duration out of bounds,
or generic phrasing) and applies targeted surgical repair.

Architecture:
  Tier 1: Intelligent LLM Surgical Rewrite (incorporates evaluator feedback directly)
  Tier 2: Deterministic Algorithmic Auto-Repair (100% failproof fallback guaranteed to pass)
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.agents.script_agent import ScenePayload, ScriptPayload
from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="script_doctor")

VALID_CATEGORIES = ["vaults", "crowds", "paperwork", "growth", "digital", "hands"]
CITATION_PHRASES = [
    "according to",
    "experts say",
    "experts claim",
    "study shows",
    "studies show",
    "research indicates",
    "research shows",
    "data suggests",
    "data reveals",
    "reports show",
    "reports suggest",
    "analysts say",
    "analysts predict",
    "sources claim",
    "as per reports",
]

BANNED_TEMPLATE_PHRASES = [
    "what you didn't see",
    "that's called",
    "here's the rule",
    "designed to stay invisible",
    "silent plunder",
    "let's dive in",
    "in this video",
    "climbing the ladder",
    "unlock your potential",
    "nine out of ten",
]


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract and parse JSON safely from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return None


class EnglishScriptRewriterAgent:
    """
    Script Doctor for the English Master Channel.
    Takes rejected scripts + evaluator critique and fixes them surgically.
    """

    def __init__(self, gemini_client=None):
        self.client = gemini_client
        if self.client is None:
            try:
                from google import genai
                from google.genai import types
                api_key = (
                    os.getenv("GEMINI_SCRIPT_API_KEY")
                    or os.getenv("GEMINI_API_KEY")
                    or os.getenv("LLM_API_KEY")
                )
                if api_key:
                    self.client = genai.Client(
                        api_key=api_key,
                        http_options=types.HttpOptions(timeout=30000)
                    )
            except Exception as exc:
                log.warning("Could not initialize genai Client in EnglishScriptRewriterAgent: %s", exc)

    def rewrite_script(
        self,
        script_data: Dict[str, Any],
        failure_reason: str,
        failure_details: Optional[Dict[str, Any]] = None,
        topic: str = "",
        target_scenes: int = 5,
    ) -> Dict[str, Any]:
        """
        Attempt an intelligent LLM rewrite with evaluator critique.
        Automatically falls back to deterministic auto_repair_script if LLM fails.
        """
        log.info("🩺 ScriptDoctor: Diagnosing English script failure — '%s'", failure_reason)

        system_prompt = (
            "You are an elite YouTube Shorts / Instagram Reels Script Doctor and Investigative Finance Editor.\n"
            "A draft video script was REJECTED by our Quality Gate Evaluator.\n"
            f"REJECTION REASON: {failure_reason}\n"
            f"FAILURE DETAILS: {failure_details or {}}\n\n"
            "YOUR TASK: Surgically rewrite and fix the script to pass all Evaluator rules while maximizing "
            "the first 3-second hold rate (>80%) and whistleblower investigative shock value.\n\n"
            "STRICT RULES FOR REWRITE:\n"
            "1. SCENE 1 COLD-OPEN HOOK (MANDATORY):\n"
            "   - Must be an aggressive, provocative cold-open question or shocking loss metric.\n"
            "   - MUST BE 5 TO 7 WORDS ONLY! (Example: 'Your bank is secretly stealing 18% GST!')\n"
            "   - Absolutely NO greetings, no throat clearing, no slow setup.\n"
            "2. ZERO CITATION LANGUAGE:\n"
            "   - Strip all phrases like 'According to', 'Experts say', 'Reports show', 'Studies suggest'.\n"
            "   - Replace with direct whistleblower confidence: 'The real truth is...'\n"
            "3. DISTINCT CINEMATIC VISUAL PROMPTS:\n"
            "   - Every scene's visual_prompt must describe a unique, high-contrast angle.\n"
            "   - Use cinematic keywords: 'macro detail', 'chiaroscuro lighting', 'shallow depth of field'.\n"
            "   - ZERO text overlays, logos, or watermarks.\n"
            "4. SEAMLESS ALGORITHMIC LOOP:\n"
            "   - Scene 5 CTA must end with a natural connector phrase leading smoothly back into Scene 1.\n"
            f"5. Target exactly {target_scenes} scenes fitting 22-26 seconds total.\n"
        )

        user_prompt = (
            f"Original Topic: {topic}\n"
            f"Draft Script to Fix:\n{json.dumps(script_data, ensure_ascii=False, indent=2)}\n\n"
            f"Please output the complete, corrected script in valid JSON matching ScriptPayload schema."
        )

        repaired = None
        if self.client:
            try:
                repaired = self._call_llm_fix(system_prompt, user_prompt)
            except Exception as exc:
                log.warning("ScriptDoctor LLM call failed: %s", exc)

        if repaired and isinstance(repaired, dict) and repaired.get("scenes"):
            log.info("✓ ScriptDoctor: Generated repaired script via LLM pass.")
            return self.auto_repair_script(repaired, failure_reason, failure_details, topic=topic)

        log.info("ScriptDoctor: Engaging deterministic auto-repair engine fallback.")
        return self.auto_repair_script(script_data, failure_reason, failure_details, topic=topic)

    def _call_llm_fix(self, system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Fast targeted LLM call with gemini models."""
        from google.genai import types
        models_to_try = [
            "gemini-3.7-flash",
            "gemini-3.1-flash-lite",
            "gemini-flash-lite-latest",
        ]
        for model_name in models_to_try:
            try:
                cfg = {
                    "temperature": 0.3,
                    "response_mime_type": "application/json",
                    "response_schema": ScriptPayload,
                    "http_options": types.HttpOptions(timeout=25000),
                }
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=[system_prompt, user_prompt],
                    config=types.GenerateContentConfig(**cfg),
                )
                if hasattr(response, "parsed") and response.parsed:
                    data = response.parsed
                    return data.model_dump() if hasattr(data, "model_dump") else data
                if hasattr(response, "text") and response.text:
                    return _extract_json(response.text)
            except Exception:
                continue
        return None

    def auto_repair_script(
        self,
        script_data: Dict[str, Any],
        failure_reason: str = "",
        failure_details: Optional[Dict[str, Any]] = None,
        topic: str = "",
    ) -> Dict[str, Any]:
        """
        Deterministic, algorithmic repair engine.
        Guarantees all evaluator gate criteria are satisfied even without LLM calls.
        """
        script = copy.deepcopy(script_data)
        scenes = script.get("scenes", [])
        if not scenes:
            return script

        # 1. Fix Scene Count Bounds (Must be between 5 and 7)
        scenes = self._fix_scene_count(scenes)

        # 2. Strip Citation Language and Banned Templates first
        scenes = self._fix_citations(scenes)

        # 3. Fix Scene 1 Hook (enforce question format, specificity, 6–11 words)
        scenes = self._fix_hook(scenes, topic=topic)

        # 4. Fix Duplicate Visual Prompts
        scenes = self._fix_prompts(scenes)

        # 5. Fix Category Rotation & B-Roll Keywords
        scenes = self._fix_categories(scenes)

        # 6. Fix Runtime / Word Count Bounds (20s to 28s)
        scenes = self._fix_duration(scenes)

        # 7. Add Seamless Loop Connector to Final Scene
        scenes = self._fix_loop_connector(scenes)

        script["scenes"] = scenes

        # 8. Fix Metadata (Title and Hashtags)
        script = self._fix_metadata(script, topic=topic)

        log.info("🛠️ ScriptDoctor: Deterministic auto-repair applied to English script.")
        return script

    def _fix_hook(
        self,
        scenes: List[Dict[str, Any]],
        max_words: int = 11,
        topic: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Ensure Scene 1 hook is:
        1. A direct question ending in "?"
        2. 6–11 words
        3. Contains "you" or "your"
        4. Specific to the topic (not generic)
        """
        if not scenes:
            return scenes

        first_scene = scenes[0]
        narration = first_scene.get("narration", "").strip()

        if not narration:
            first_scene["narration"] = self._make_question_from_topic(topic)
            return scenes

        # Extract the first sentence/clause as the hook
        first_clause = re.split(r"[.!—\n]", narration)[0].strip()
        original_had_anchor = self._has_shock_anchor(first_clause)
        words = first_clause.split()

        # Enforce word count: 6–11 words. Trim filler words first so a
        # shock anchor (number, named entity) survives the cut; hard-cut
        # only as a last resort.
        if len(words) > max_words:
            words = self._trim_filler(words, max_words)
        if len(words) > max_words:
            first_clause = " ".join(words[:max_words]).rstrip("?,!.")
        else:
            first_clause = " ".join(words)
        if len(words) < 6:
            first_scene["narration"] = self._make_question_from_topic(topic)
            log.info("  ↳ ScriptDoctor: Hook too short (%d words); replaced with topic-specific question.", len(words))
            return scenes

        # Enforce question format
        if not first_clause.endswith("?"):
            first_clause = self._convert_to_question(first_clause, topic)

        # Enforce "you"/"your" presence
        if "you" not in first_clause.lower() and "your" not in first_clause.lower():
            first_clause = first_clause.rstrip("?") + " — is this hurting your money?"

        # Reject and replace generic questions
        if self._is_generic_question(first_clause):
            replacement = self._make_question_from_topic(topic)
            log.info(
                "  ↳ ScriptDoctor: Generic question '%s' replaced with topic-specific: '%s'",
                first_clause[:50], replacement[:60],
            )
            first_scene["narration"] = replacement
            return scenes

        # First-2-seconds rule: the hook must still carry a shock anchor if
        # the model wrote one. Never invent numbers - only preserve or reuse
        # an anchor that already exists in the model output or topic.
        if original_had_anchor and not self._has_shock_anchor(first_clause):
            log.warning(
                "  ↳ ScriptDoctor: shock anchor (number/name) was lost in repair; restoring model's original hook wording.",
            )
            restored = " ".join(re.split(r"[.!—\n]", narration)[0].strip().split()[:max_words]).rstrip("?,!")
            if not restored.endswith("?"):
                restored = self._convert_to_question(restored, topic)
            if self._has_shock_anchor(restored) and not self._is_generic_question(restored):
                first_clause = restored

        first_scene["narration"] = first_clause
        log.info("  ↳ ScriptDoctor: Scene 1 question hook finalised: '%s'", first_clause)
        return scenes

    _FILLER_WORDS = {"actually", "really", "just", "even", "basically", "literally", "simply", "quite"}

    @staticmethod
    def _trim_filler(words: List[str], max_words: int) -> List[str]:
        """Drop low-value filler words, keeping every number and named anchor."""
        trimmed = [w for w in words if w.lower().strip("?,!.") not in EnglishScriptRewriterAgent._FILLER_WORDS]
        # Never trim below a valid hook; if filler removal went too far, keep the original.
        if len(trimmed) < 6:
            return words
        return trimmed

    @staticmethod
    def _has_shock_anchor(text: str) -> bool:
        """True when the hook carries a verifiable shock anchor for the first 2 seconds:
        a number/statistic, a ₹/% figure, an acronym like SEBI/RBI, or a named entity."""
        if re.search(r"\d", text):
            return True
        if "₹" in text or "%" in text:
            return True
        # Acronyms (SEBI, RBI, IPO, EMI) and mid-sentence capitalized names (Buffett, Titan)
        tokens = text.replace("?", "").split()
        for i, tok in enumerate(tokens):
            core = tok.strip(',.:;!' + chr(34) + chr(39))
            if len(core) >= 2 and core.isupper():
                return True
            if i > 0 and core[:1].isupper() and core[1:].islower() and core.lower() not in {"is", "are", "do", "did", "your", "you", "why", "how", "what"}:
                return True
        return False

    def _convert_to_question(self, statement: str, topic: str = "") -> str:
        """Convert a declarative statement to a personal-implication question."""
        statement = statement.rstrip("!.")
        lower = statement.lower()

        question_starters = {"is", "are", "do", "did", "does", "was", "were", "why", "how", "what", "can", "could"}
        first_word = lower.split()[0] if lower.split() else ""

        if first_word in question_starters:
            return statement + "?"

        if lower.startswith("your "):
            return "Is " + lower + "?"

        if lower.startswith("stop "):
            rest = statement[5:]
            return f"Are you still {rest.lower()}?"

        # Fallback to topic-specific question
        return self._make_question_from_topic(topic)

    def _is_generic_question(self, question: str) -> bool:
        """Return True if the question is too generic to be useful."""
        GENERIC_PATTERNS = [
            "did you know",
            "have you ever",
            "are you aware",
            "do you want",
            "have you heard",
        ]
        lower = question.lower()
        return any(p in lower for p in GENERIC_PATTERNS)

    def _make_question_from_topic(self, topic: str = "") -> str:
        """
        Generate a topic-specific fallback question from the topic string.
        Maps 25+ financial trap categories to pre-validated question templates.
        """
        from src.agents.question_agent import TOPIC_QUESTION_MAP
        topic_lower = topic.lower()

        # Find best match by longest matching keyword
        matched = []
        for keyword, question in TOPIC_QUESTION_MAP.items():
            if keyword in topic_lower:
                matched.append((len(keyword), question))
        if matched:
            matched.sort(key=lambda x: x[0], reverse=True)
            return matched[0][1]

        # Last resort — still specific-sounding
        words = [w for w in topic.split() if len(w) > 4]
        subject = words[0].lower() if words else "investment"
        return f"Is your {subject} secretly working against your money right now?"


    def _fix_citations(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove journalistic citations and passive hedging."""
        for s in scenes:
            text = s.get("narration", "")
            # Replace citations with whistleblower cadence
            for phrase in CITATION_PHRASES:
                if phrase.lower() in text.lower():
                    pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
                    text = pattern.sub("the hidden truth is", text)

            # Strip banned generic templates
            for template in BANNED_TEMPLATE_PHRASES:
                if template.lower() in text.lower():
                    pattern = re.compile(rf"\b{re.escape(template)}\b", re.IGNORECASE)
                    text = pattern.sub("", text)

            s["narration"] = " ".join(text.split()).strip(" ,.-")
        return scenes

    def _fix_prompts(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure all visual prompts are strictly unique with rich cinematic directives."""
        seen_prompts = set()
        camera_modifiers = [
            "Cinematic dynamic close-up angle, dramatic chiaroscuro lighting, tactile paper texture",
            "Wide architectural establishing shot, high-contrast trading terminal reflections",
            "Over-the-shoulder perspective framing, moody atmospheric depth of field, 35mm film grain",
            "Low-angle heroic framing, sleek modern banking vault steel aesthetic, sharp focus",
            "Macro detail focus on physical financial currency, rich cinematic contrast",
            "Dynamic motion tracking camera perspective, cool tone industrial finance lighting",
        ]
        for i, s in enumerate(scenes):
            prompt = s.get("visual_prompt", "").strip()
            if not prompt or prompt in seen_prompts or len(prompt.split()) < 15:
                mod = camera_modifiers[i % len(camera_modifiers)]
                s["visual_prompt"] = f"{prompt}, {mod}".strip(", ")
                log.info("  ↳ ScriptDoctor: Differentiated scene %d visual prompt with cinematic modifier", i + 1)
            seen_prompts.add(s["visual_prompt"])
        return scenes

    def _fix_categories(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure broll_keyword rotates and aligns with valid categories."""
        broll_category_map = {
            "vaults": "bank vault opening",
            "crowds": "stock exchange crowd",
            "paperwork": "signing financial contract",
            "growth": "green profit chart",
            "digital": "red falling stock market screen",
            "hands": "counting cash money",
        }
        for i, s in enumerate(scenes):
            cat = VALID_CATEGORIES[i % len(VALID_CATEGORIES)]
            if not s.get("broll_keyword"):
                s["broll_keyword"] = broll_category_map[cat]
        return scenes

    def _fix_scene_count(self, scenes: List[Dict[str, Any]], target_count: int = 5) -> List[Dict[str, Any]]:
        """Ensure scene count is between 5 and 6 scenes."""
        if len(scenes) < 5:
            while len(scenes) < 5:
                idx = len(scenes) + 1
                new_scene = copy.deepcopy(scenes[-1])
                new_scene["scene_id"] = idx
                new_scene["narration"] = "Here is the calculation banks never explain."
                new_scene["visual_prompt"] = "Macro lens on glowing calculator screen, cinematic contrast, 35mm film grain"
                new_scene["broll_keyword"] = "financial calculation"
                scenes.append(new_scene)
        elif len(scenes) > 6:
            scenes = scenes[:6]

        # Normalize scene_ids sequentially (1-indexed)
        for i, s in enumerate(scenes):
            s["scene_id"] = i + 1
        return scenes

    def _fix_duration(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Ensure estimated duration (word_count / 2.3) is within 20.0s - 27.0s.
        Clamps excessively short (<45 words) or long (>75 words) scripts.
        """
        full_text = " ".join(s.get("narration", "") for s in scenes)
        words = full_text.split()
        total_words = len(words)

        if total_words < 45:
            # Add short expansion clause to middle scenes
            for s in scenes[1:4]:
                s_words = s.get("narration", "").split()
                if len(s_words) < 14:
                    s["narration"] = f"{s.get('narration', '').rstrip('.!')} — and this trap is completely hidden."
                full_text = " ".join(sc.get("narration", "") for sc in scenes)
                if len(full_text.split()) >= 52:
                    break
        elif total_words > 75:
            # Trim narrations gently per scene
            for s in scenes:
                s_words = s.get("narration", "").split()
                if len(s_words) > 13:
                    s["narration"] = " ".join(s_words[:13]) + "!"
                full_text = " ".join(sc.get("narration", "") for sc in scenes)
                if len(full_text.split()) <= 65:
                    break
        return scenes

    def _fix_loop_connector(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Ensure final scene CTA ends with a seamless curiosity loop phrase
        that links smoothly back into Scene 1 upon auto-replay, while satisfying
        the spoken comment CTA requirement. Also guarantees 2nd-person address.
        """
        if not scenes:
            return scenes

        last_scene = scenes[-1]
        narration = last_scene.get("narration", "").strip()

        has_cta = any(
            phrase in narration.lower()
            for phrase in ["comment 'guide'", "comment guide", "comment below", "save this", "share this", "share with"]
        )
        if not has_cta:
            narration = f"{narration.rstrip('.!')}. Share this and comment GUIDE below"

        loop_connectors = [
            "— which is why you must avoid this:",
            "— and that leads directly to the biggest trap:",
            "— which explains the exact reason why:",
        ]

        if not any(conn.rstrip(":") in narration for conn in loop_connectors):
            # Attach a clean loop connector if not already present
            connector = loop_connectors[0]
            clean_base = narration.rstrip(".!")
            narration = f"{clean_base} {connector}"
            log.info("  ↳ ScriptDoctor: Engineered seamless loop connector on final scene.")

        last_scene["narration"] = narration

        # Ensure at least 2 scenes have second-person pronouns ('you' or 'your')
        you_count = sum(1 for s in scenes if "you" in s.get("narration", "").lower() or "your" in s.get("narration", "").lower())
        if you_count < 2:
            for s in scenes[1:]:
                n_text = s.get("narration", "")
                if "you" not in n_text.lower() and "your" not in n_text.lower():
                    s["narration"] = f"Watch your money closely: {n_text}"
                    you_count += 1
                    if you_count >= 2:
                        break

        return scenes

    def _fix_metadata(self, script_data: Dict[str, Any], topic: str = "") -> Dict[str, Any]:
        """Ensure title and hashtags adhere to high-reach formats."""
        if not script_data.get("title") or ":" not in script_data.get("title", ""):
            clean_topic = topic[:28] if topic else "Money Trap"
            script_data["title"] = f"{clean_topic}: Hidden Trap Exposed #Shorts"

        if not script_data.get("description"):
            script_data["description"] = "Exposing hidden financial traps and secret banking fees. #Finance #Shorts #MarketDebunk"

        if not script_data.get("hashtags") or len(script_data.get("hashtags", [])) < 3:
            script_data["hashtags"] = ["#Finance", "#Shorts", "#MoneyTips", "#MarketDebunk"]

        return script_data
