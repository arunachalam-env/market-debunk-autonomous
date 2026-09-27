"""
src/analytics/tuner_agent.py

Algorithmic Loop Fine-Tuning Agent for Market Debunk Pipeline.
Analyzes 48-hour social metrics across YouTube, Instagram, and Facebook:
1. Compares top-quartile vs. bottom-quartile videos (views, retention/APV, shares, saves).
2. Dynamically calibrates optimal video duration and spoken word count.
3. Formulates winning hook structures and topic resonance directives.
4. Generates data/tuning_playbook.json to close the feedback loop for the next video.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="loop_tuner")


class PerformanceTuningAgent:
    """Extracts learnings from social analytics to continuously optimize future video generation."""

    def __init__(
        self,
        analytics_path: Optional[Path] = None,
        publish_path: Optional[Path] = None,
        playbook_path: Optional[Path] = None,
    ):
        data_dir = settings.DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)

        self.analytics_path = Path(analytics_path) if analytics_path else data_dir / "analytics_ledger.json"
        self.publish_path = Path(publish_path) if publish_path else data_dir / "publish_ledger.json"
        self.playbook_path = Path(playbook_path) if playbook_path else data_dir / "tuning_playbook.json"

    def load_analytics_records(self) -> list[dict]:
        """Load audited analytics history."""
        if not self.analytics_path.is_file():
            return []
        try:
            with open(self.analytics_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as exc:
            log.warning("Could not read analytics ledger: %s", exc)
            return []

    @staticmethod
    def _has_real_metrics(record: dict) -> bool:
        """True only when an audit row carries at least one real platform metric.

        Rows written before the analytics-sensor fix (and rows marked
        "fetch_failed") have empty instagram/youtube dicts - counting them made
        the tuner "learn" from zeros.
        """
        if record.get("status") == "fetch_failed":
            return False
        for platform in ("instagram", "youtube"):
            metrics = record.get(platform) or {}
            if any(v not in (None, 0, "") for v in metrics.values()):
                return True
        return False

    def load_publish_ledger(self) -> list[dict]:
        """Load published video metadata."""
        if not self.publish_path.is_file():
            return []
        try:
            with open(self.publish_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as exc:
            log.warning("Could not read publish ledger: %s", exc)
            return []

    def load_playbook(self) -> dict:
        """Load existing tuning playbook."""
        if not self.playbook_path.is_file():
            return self._default_playbook()
        try:
            with open(self.playbook_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self._default_playbook()

    def generate_playbook(self) -> dict:
        """
        Synthesize analytics records into an actionable creative tuning playbook.
        Updates data/tuning_playbook.json.
        """
        records = self.load_analytics_records()
        usable = [r for r in records if self._has_real_metrics(r)]
        empty_rows = len(records) - len(usable)
        log.info(
            "Running Performance Tuning Agent across %d historical analytics records (%d usable, %d empty/failed ignored)...",
            len(records), len(usable), empty_rows,
        )

        # Baseline defaults until we have REAL metrics. Empty audit rows must never
        # steer the playbook - that was the "learning from nothing" bug.
        if len(usable) < 2:
            log.warning(
                "Only %d usable analytics record(s) with real metrics (< 2); keeping calibrated baseline playbook. "
                "Fix the analytics credentials (see sensor logs) so the loop can start learning.",
                len(usable),
            )
            playbook = self._default_playbook()
            playbook["last_updated"] = datetime.now(timezone.utc).isoformat()
            playbook["analyzed_videos_count"] = len(usable)
            playbook["data_status"] = "waiting_for_real_analytics"
            self._save_playbook(playbook)
            return playbook

        # Rank videos by composite viral engagement score
        scored_videos = []
        for r in usable:
            ig = r.get("instagram", {})
            yt = r.get("youtube", {})
            views = yt.get("views", 0) or ig.get("reach", 0) or 1
            saves = ig.get("saved", 0)
            shares = ig.get("shares", 0)
            likes = yt.get("likes", 0) + ig.get("total_interactions", 0)
            # Viral Action Score weights active intent (saves & shares) highest
            viral_score = (shares * 3.0 + saves * 2.5 + likes * 1.0) / max(1.0, float(views) / 100.0)

            scored_videos.append({
                "title": r.get("title", ""),
                "hook": r.get("hook", ""),
                "topic": r.get("topic", ""),
                "duration": float(r.get("duration_seconds") or 25.0),
                "viral_score": viral_score,
                "views": views,
                "saves": saves,
                "shares": shares,
            })

        scored_videos.sort(key=lambda x: x["viral_score"], reverse=True)
        top_cohort = scored_videos[:max(1, len(scored_videos) // 2)]
        bottom_cohort = scored_videos[max(1, len(scored_videos) // 2):]

        # Calculate calibrated runtime
        avg_top_duration = sum(v["duration"] for v in top_cohort) / len(top_cohort)
        calibrated_duration = max(22.0, min(28.0, round(avg_top_duration, 1)))
        # Word count at ~155 wpm for calibrated duration
        calibrated_words = int(calibrated_duration * (155.0 / 60.0))

        # Use Gemini flash-lite to synthesize strategic creative insights
        synthesized_insights = self._synthesize_with_gemini(top_cohort, bottom_cohort)

        playbook = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "analyzed_videos_count": len(usable),
            "data_status": "learning_from_real_analytics",
            "optimal_runtime_seconds": calibrated_duration,
            "optimal_word_count": calibrated_words,
            "top_performing_topics": synthesized_insights.get("top_topics", ["hidden charges", "EMI trap", "F&O lot size margin traps", "credit card tricks"]),
            "winning_hook_formulas": synthesized_insights.get("winning_hooks", [
                "Open with the exact Rupee amount quietly lost: 'Your bank took ₹X without an alert'",
                "Contradict popular wisdom: 'Why your zero-cost EMI is actually charging 18% GST'"
            ]),
            "script_directives": synthesized_insights.get("script_directives", [
                "Keep Scene 1 under 8 words for immediate hook velocity.",
                "Introduce the financial trap mechanism by Scene 3.",
                "Always end with a dual CTA: save the video and comment 'GUIDE'."
            ]),
            "seo_directives": synthesized_insights.get("seo_directives", [
                "Prioritize problem-aware hashtags (#MoneyMistakes, #HiddenFees).",
                "Ensure caption line 1 matches spoken hook word-for-word."
            ]),
        }

        self._save_playbook(playbook)
        log.info(
            "✅ Performance Tuning Playbook generated: optimal duration %.1fs, target words %d.",
            calibrated_duration,
            calibrated_words,
        )
        return playbook

    def get_script_directives_prompt(self) -> str:
        """Format the active playbook rules for injection into ScriptAgent system prompt."""
        playbook = self.load_playbook()
        directives = playbook.get("script_directives", [])
        hooks = playbook.get("winning_hook_formulas", [])
        words = playbook.get("optimal_word_count", 65)
        duration = playbook.get("optimal_runtime_seconds", 25.0)

        directives_str = "\n".join(f"  • {d}" for d in directives)
        hooks_str = "\n".join(f"  • {h}" for h in hooks[:3])

        return (
            f"\n\nALGORITHMIC TUNING PLAYBOOK (Continuous Loop Analytics Guidance):\n"
            f"Target Runtime: ~{duration:.0f}s | Target Narration Words: {words} words total across ~8 scenes.\n"
            f"Proven Winning Hook Archetypes:\n{hooks_str}\n"
            f"Creative Execution Directives:\n{directives_str}\n"
        )

    def _default_playbook(self) -> dict:
        return {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "analyzed_videos_count": 0,
            "optimal_runtime_seconds": 24.0,
            "optimal_word_count": 62,
            "top_performing_topics": ["hidden charges", "F&O lot size margin traps", "IPO allotment hype", "EMI trap", "credit card tricks"],
            "winning_hook_formulas": [
                "Open on concrete financial damage: 'Your bank just charged you ₹X and you didn't notice'",
                "Pattern interrupt: 'Stop buying Nifty index funds until you see this silent deduction'"
            ],
            "script_directives": [
                "Scene 1 must be under 8 words spoken in under 3 seconds.",
                "Show real mathematical loss in Scene 3 (₹ lost or % spread).",
                "End on explicit save & share trigger in the voiceover."
            ],
            "seo_directives": [
                "Rotate between debunk, wealth, and alert hashtag clusters.",
                "First line hook in Instagram caption must be under 80 characters."
            ],
        }

    def _save_playbook(self, playbook: dict) -> None:
        try:
            with open(self.playbook_path, "w", encoding="utf-8") as f:
                json.dump(playbook, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.error("Failed to save tuning playbook: %s", exc)

    def _synthesize_with_gemini(self, top_cohort: list[dict], bottom_cohort: list[dict]) -> dict:
        """Call Gemini to compare winners vs losers and extract actionable creative rules."""
        api_key = getattr(settings, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return {}

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = f"""You are an elite short-form algorithmic analyst for YouTube Shorts and Instagram Reels.
Analyze these top-performing vs under-performing Indian finance video records:

TOP PERFORMING VIDEOS (High retention, high shares/saves):
{json.dumps(top_cohort[:5], indent=2)}

UNDER PERFORMING VIDEOS (Low retention, drop-offs):
{json.dumps(bottom_cohort[:5], indent=2)}

Synthesize actionable creative rules for the NEXT video to maximize retention and shares.
Output strict JSON with these exact keys:
{{
  "top_topics": ["2-3 financial sub-niches that outperform"],
  "winning_hooks": ["2 concrete hook formulas that drive high retention"],
  "script_directives": ["3 short, punchy rules for scene pacing and narration"],
  "seo_directives": ["2 rules for caption and hashtag selection"]
}}"""

            resp = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=600, response_mime_type="application/json"),
            )
            if resp and resp.text:
                return json.loads(resp.text)
        except Exception as exc:
            log.debug("Gemini performance synthesis skipped: %s", exc)

        return {}
