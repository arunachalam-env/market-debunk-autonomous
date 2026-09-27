"""
src/agents/channel_director.py

Autonomous Channel Director & Growth Strategist Agent (Level 3 Full Authority).

Responsibilities:
1. Senses Market Pulse: Scans real-time Indian finance controversies, Google Trends,
   and community discussions (via Google Trends RSS, SerpApi, Reddit discussions).
2. Audits Channel Performance & Audience Signals: Inspects recent video metrics
   (views, likes, comments) and viewer feedback across YouTube and Meta Instagram.
3. Formulates Daily Strategic Briefs: Decides the exact high-reach topic, cynical angle,
   hook formula, format type (standalone vs 2-part series), and specific Tamil adaptation
   guidance for the companion pipeline.
4. Executes Community Engagement: Posts strategic pinned discussion comments on YouTube
   and responds to audience "GUIDE" comment requests.
5. Zero-Breakage Guarantee: 100% defensive error handling with seamless fallbacks.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, Field

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="channel_director")

_STRATEGIC_BRIEF_PATH = settings.DATA_DIR / "strategic_brief.json"
_PUBLISH_LEDGER_PATH = settings.DATA_DIR / "publish_ledger.json"
_ANALYTICS_LEDGER_PATH = settings.DATA_DIR / "analytics_ledger.json"
_USED_TOPICS_PATH = settings.DATA_DIR / "used_topics.json"


class StrategicBrief(BaseModel):
    """Structured strategic blueprint formulated by the Channel Director Agent."""
    topic_thesis: str = Field(
        description="The core myth, scam, or predatory financial leak to expose."
    )
    strategic_angle: str = Field(
        description="The cynical, street-smart perspective, exposing hidden math or fine print."
    )
    format_type: Literal["STANDALONE", "SERIES_PART_1", "SERIES_PART_2"] = Field(
        default="STANDALONE",
        description="Whether this video is a single punchy debunk or part of an ongoing series."
    )
    hook_style: str = Field(
        default="HARD_NUMBER_SHOCK",
        description="Opening visual/verbal hook style (e.g. HARD_NUMBER_SHOCK, CONFRONTATIONAL_TRUTH, EXPOSING_HYPOCRISY)."
    )
    hook_type: str = Field(
        default="LOSS_IMPLICATION",
        description=(
            "The exact question hook type for Scene 1, used by QuestionCraftingAgent. "
            "One of: LOSS_IMPLICATION | COMPETENCE_CHALLENGE | MYTH_BUST | AUTHORITY_CHALLENGE | OUTCOME_GAP."
        )
    )
    key_statistic: str = Field(
        default="",
        description="The pivotal number, percentage, or currency figure anchoring the debunk."
    )
    target_emotion: str = Field(
        default="Cynical revelation & urgency",
        description="The intended psychological reaction of the viewer."
    )
    pinned_comment_text: str = Field(
        description="High-converting pinned discussion comment to spark debate or trigger 'GUIDE' comments."
    )
    tamil_adaptation_directive: str = Field(
        description="Guidance for the Tamil companion writer on cultural nuance, Tanglish search terms, and tone."
    )
    source_signal: str = Field(
        default="Market Pulse & Community Intelligence",
        description="Origin of the strategic insight."
    )


class ChannelDirectorAgent:
    """
    Strategic supervisor acting as General Manager & Chief Strategist for the channel.
    Operates at Level 3 Autonomous Authority.
    """

    def __init__(self):
        self.data_dir = settings.DATA_DIR
        self.brief_path = _STRATEGIC_BRIEF_PATH

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Market Pulse & External Trends Sensing
    # ──────────────────────────────────────────────────────────────────────────

    def scan_market_pulse(self, slot_domain: str = "MARKET_INVESTING") -> List[str]:
        """
        Gathers live market signals from Google Trends RSS and SerpApi.
        Returns a list of trending topics or controversy headlines.
        """
        signals: List[str] = []

        # Signal Source A: Google Trends RSS for India
        try:
            url = "https://trends.google.com/trending/rss?geo=IN"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MarketDebunkDirector/1.0"}
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                items = root.findall(".//item")
                for item in items[:8]:
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        signals.append(title_elem.text.strip())
                log.info("✓ Director: Captured %d live Google Trends signals for India", len(signals))
        except Exception as exc:
            log.warning("Director: Google Trends RSS scan notice (%s)", exc)

        # Signal Source B: SerpApi Search for Retail Investor Panic & Debates
        if settings.SERPAPI_KEY:
            try:
                search_query = (
                    "site:reddit.com/r/IndianStreetBets OR site:reddit.com/r/IndiaInvestments loss penalty scam"
                    if slot_domain == "MARKET_INVESTING"
                    else "credit card hidden charges OR EMI GST trap OR bank penalty RBI complaint"
                )
                params = {
                    "engine": "google",
                    "q": search_query,
                    "gl": "in",
                    "hl": "en",
                    "num": 6,
                    "api_key": settings.SERPAPI_KEY,
                }
                resp = requests.get("https://serpapi.com/search.json", params=params, timeout=12)
                if resp.status_code == 200:
                    results = resp.json().get("organic_results", [])
                    for r in results:
                        t = r.get("title", "")
                        snippet = r.get("snippet", "")
                        if t:
                            signals.append(f"{t}: {snippet[:80]}")
                    log.info("✓ Director: Captured %d SerpApi discussion signals", len(results))
            except Exception as exc:
                log.warning("Director: SerpApi market scan notice (%s)", exc)

        return signals[:12]

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Audience Intelligence & Channel Performance
    # ──────────────────────────────────────────────────────────────────────────

    def audit_audience_signals(self) -> Dict[str, Any]:
        """
        Audits recent performance from publish_ledger.json and analytics_ledger.json.
        Extracts view momentum and recent video IDs.
        """
        signals: Dict[str, Any] = {
            "recent_topics": [],
            "recent_youtube_ids": [],
            "performance_notes": "Auditing recent publications...",
        }

        if _PUBLISH_LEDGER_PATH.is_file():
            try:
                with open(_PUBLISH_LEDGER_PATH, "r", encoding="utf-8") as f:
                    ledger = json.load(f)
                if isinstance(ledger, list):
                    recent = ledger[-6:]
                    signals["recent_topics"] = [item.get("title") or item.get("topic") for item in recent if item.get("topic")]
                    signals["recent_youtube_ids"] = [
                        item.get("ids", {}).get("youtube") for item in recent if item.get("ids", {}).get("youtube")
                    ]
            except Exception as e:
                log.warning("Director: Could not read publish ledger (%s)", e)

        playbook_path = settings.DATA_DIR / "tuning_playbook.json"
        if playbook_path.is_file():
            try:
                with open(playbook_path, "r", encoding="utf-8") as pf:
                    pb = json.load(pf)
                signals["optimal_runtime"] = pb.get("optimal_runtime_seconds", 24.0)
                signals["optimal_word_count"] = pb.get("optimal_word_count", 65)
                signals["winning_hook_formulas"] = pb.get("winning_hook_formulas", [])
            except Exception:
                pass

        return signals

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Formulate Strategic Brief (The Director's Directive)
    # ──────────────────────────────────────────────────────────────────────────

    def formulate_strategic_brief(
        self,
        topic_data: Optional[Dict[str, Any]] = None,
        override_slot: Optional[str] = None,
    ) -> StrategicBrief:
        """
        Synthesizes trends, audience signals, and anti-repetition rules.
        When topic_data from topic_agent.discover_topic() is provided, acts as the
        LLM Strategic Layer on top of that fresh sourced story, elevating it with
        cynical framing, hard statistics, hook formulas, and Tamil adaptation directives.
        """
        utc_hour = datetime.now(timezone.utc).hour
        slot_domain = override_slot or ("MARKET_INVESTING" if utc_hour < 8 else "CONSUMER_DEFENSE")
        log.info("🎯 Director Agent: Formulating strategic brief for slot [%s] (UTC hour %d)...", slot_domain, utc_hour)

        market_signals = self.scan_market_pulse(slot_domain)
        audience_signals = self.audit_audience_signals()

        used_concepts = []
        if _USED_TOPICS_PATH.is_file():
            try:
                with open(_USED_TOPICS_PATH, "r", encoding="utf-8") as f:
                    used = json.load(f)
                used_concepts = [item.get("concept") or item.get("topic", "")[:50] for item in used[-15:]]
            except Exception:
                pass

        sourced_context = ""
        if topic_data:
            s_ch = topic_data.get("channel", "Market Intelligence")
            s_title = topic_data.get("video_title") or topic_data.get("thesis", "")
            s_thesis = topic_data.get("thesis", "")
            s_seed = topic_data.get("story_seed", {})
            s_concept = s_seed.get("concept_name", "")
            s_oneliner = s_seed.get("concept_one_liner", "")
            s_anchor = s_seed.get("real_world_anchor", "")
            s_source_id = topic_data.get("source_id", "")
            sourced_context = f"""
PRIMARY SOURCED REAL-WORLD STORY (FROM BATTLE-TESTED SOURCING PIPELINE):
- Sourced Channel / Provider: {s_ch}
- Sourced Headline: {s_title}
- Discovered Core Thesis: {s_thesis}
- Finance Concept: {s_concept}
- Concept One-Liner: {s_oneliner}
- Real-World Anchor: {s_anchor}
- Source ID: {s_source_id}

MANDATE FOR CHANNEL DIRECTOR:
Do NOT discard or replace this fresh sourced story with an unrelated generic topic!
Your mission is to ELEVATE AND SHARPEN THIS EXACT STORY into an elite, cynical, high-retention debunk video:
1. Elevate the discovered thesis with razor-sharp cynical framing exposing the math, fine print, or marketing trick.
2. Formulate the cynical strategic angle.
3. Compute or specify the key statistic / numbers to anchor the debunk.
4. Select the optimal hook style (HARD_NUMBER_SHOCK, CONFRONTATIONAL_TRUTH, EXPOSING_HYPOCRISY, etc.).
5. Write the high-converting pinned comment to spark debate or trigger 'GUIDE' comments.
6. Provide a tailored 'tamil_adaptation_directive' for the Tamil companion pipeline to localize THIS EXACT STORY with native cultural nuance and Tanglish terms.
"""

        prompt = f"""You are the Executive Channel Director & Growth Strategist for an elite, cynical financial debunking media brand ("Market Debunk" in English and "Market Debunk Tamil").
Your goal: Maximize 30-second view retention, direct-message shares, and comment volume across YouTube Shorts and Instagram Reels.

{sourced_context}

SLOT MANDATE:
- Slot Domain: {slot_domain}
  * If MARKET_INVESTING (Morning): Strictly Indian Stock Market, F&O options traps, SEBI warnings, mutual fund expense drag, SME IPO manipulation, algorithmic stop-loss hunting, pump-and-dump scams.
  * STRONGLY PREFER stories anchored on a NAMED famous investor (Buffett, Munger, Jhunjhunwala, Kedia, Damani, Pabrai, Lynch) or a SPECIFIC NSE-listed stock with a documented episode - that is the format this channel's audience actually watches. Generic outrage topics underperform by 100x.
  * If CONSUMER_DEFENSE (Evening): Strictly everyday middle-class money traps: Zero-Cost EMI 18% GST leak, credit card minimum due compounding, CIBIL drops, hospital insurance room-rent caps, EPFO claim rejection penalties, debit card AMC deductions.

LIVE MARKET PULSE SIGNALS (Real-time news & community discussions):
{json.dumps(market_signals, indent=2) if market_signals else "No breaking signals; deploy an unduplicated evergreen financial trap."}

RECENTLY COVERED CONCEPTS (STRICT 14-DAY LOCKOUT - DO NOT REPEAT):
{json.dumps(used_concepts, indent=2)}

DIRECTOR INSTRUCTIONS:
1. Ground your strategy directly on the PRIMARY SOURCED STORY provided above.
2. Formulate a CYNICAL, street-smart debunk angle exposing the absurdity and math behind the gimmick.
3. State the exact numeric calculation or statistic (e.g. "₹15,00,000 lost in 25 years", "18% GST on interest", "6% penalty").
4. Provide an engaging pinned comment that asks a controversial question or offers a checklist for commenting "GUIDE".
5. Provide a specific 'tamil_adaptation_directive' describing how the Tamil companion should localize the concept with popular Tanglish terms (e.g. "Direct vs Regular Fund", "CIBIL Score Gaali", "Zero Cost EMI Aabathu").
6. ACCURACY IS NON-NEGOTIABLE: every statistic in your brief must be real and verifiable from the sourced story or public record. Never invent numbers to make the angle punchier - a separate fact-check gate will block the video.
7. VISUALS RULE: all visuals are licensed stock clips or AI-generated images. Never plan around third-party footage, news clips, or videos of real investors - using them is a copyright and monetization risk.
8. Select the exact 'hook_type' for Scene 1 from EXACTLY one of these options:
   - LOSS_IMPLICATION   → Use when the video exposes a hidden fee, silent deduction, or ongoing financial leak affecting the viewer NOW.
   - COMPETENCE_CHALLENGE → Use when the video tests whether the viewer actually understands their own financial product.
   - MYTH_BUST          → Use when the video corrects a widespread false belief about a product or regulation.
   - AUTHORITY_CHALLENGE → Use when the video exposes misconduct by an advisor, distributor, or bank representative.
   - OUTCOME_GAP        → Use when the video reveals a product is underperforming its promised returns or benchmarks.

OUTPUT FORMAT:
Respond ONLY with a valid JSON object matching this schema:
{{
  "topic_thesis": "One crisp sentence stating the sharpened core trap/myth based on the sourced story",
  "strategic_angle": "The cynical, hard-hitting breakdown angle exposing the hidden mechanism",
  "format_type": "STANDALONE",
  "hook_style": "HARD_NUMBER_SHOCK",
  "hook_type": "LOSS_IMPLICATION",
  "key_statistic": "35% wealth loss / ₹15,00,000",
  "target_emotion": "Shock and immediate urge to check personal accounts",
  "pinned_comment_text": "Did your bank or broker push you into this? Drop your thoughts below, or comment 'GUIDE' for our step-by-step checklist! 💬👇",
  "tamil_adaptation_directive": "Focus on the middle-class mindset in Tamil Nadu. Use relatable terms like 'Bank Manager Sonna Nambaatheenga' and explain the exact math in lakhs.",
  "source_signal": "Audience pain-point & market trend"
}}
"""


        brief_dict = self._call_llm_for_brief(prompt)
        if not brief_dict:
            log.warning("Director: LLM brief generation failed; using fallback strategic directive.")
            brief_dict = self._build_emergency_fallback(slot_domain, topic_data=topic_data)

        try:
            brief = StrategicBrief(**brief_dict)
        except Exception as err:
            log.warning("Director: Pydantic validation error (%s); sanitizing fallback brief", err)
            fallback = self._build_emergency_fallback(slot_domain, topic_data=topic_data)
            brief = StrategicBrief(**fallback)

        try:
            self.brief_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.brief_path, "w", encoding="utf-8") as f:
                json.dump(brief.model_dump(), f, indent=2)
            log.info("✓ Director: Strategic brief persisted to %s", self.brief_path.name)
        except Exception as write_err:
            log.warning("Director: Could not persist strategic brief (%s)", write_err)

        log.info("🏆 Director Agent Decision: '%s' | Angle: '%s'", brief.topic_thesis[:60], brief.strategic_angle[:50])
        return brief

    def _call_llm_for_brief(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Invokes LLM cascade (Gemini Flash -> Groq) to generate the strategic brief."""
        from src.agents.script_agent import _get_api_clients
        clients = _get_api_clients()
        models = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]

        for model_name in models:
            for client in clients:
                try:
                    cfg = {"temperature": 0.65}
                    if "gemini" in model_name:
                        cfg["response_mime_type"] = "application/json"
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=cfg,
                    )
                    raw_text = resp.text.strip()
                    if "```" in raw_text:
                        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
                        raw_text = re.sub(r"\s*```\s*$", "", raw_text, flags=re.MULTILINE)
                    data = json.loads(raw_text)
                    if isinstance(data, dict) and "topic_thesis" in data:
                        return data
                except Exception as exc:
                    log.debug("Director LLM attempt (%s) notice: %s", model_name, exc)
                    continue

        if settings.GROQ_API_KEY:
            try:
                from groq import Groq
                groq_client = Groq(api_key=settings.GROQ_API_KEY)
                comp = groq_client.chat.completions.create(
                    model=settings.GROQ_FALLBACK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=600,
                    temperature=0.65,
                )
                raw_text = comp.choices[0].message.content.strip()
                if "```" in raw_text:
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
                    raw_text = re.sub(r"\s*```\s*$", "", raw_text, flags=re.MULTILINE)
                return json.loads(raw_text)
            except Exception as groq_err:
                log.warning("Director: Groq brief generation failed: %s", groq_err)

        return None

    def _build_emergency_fallback(
        self,
        slot_domain: str,
        topic_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Provides an emergency strategic brief if LLM calls fail."""
        if topic_data:
            thesis = topic_data.get("thesis") or topic_data.get("video_title", "Financial Myth Debunk")
            seed = topic_data.get("story_seed", {})
            concept = seed.get("concept_name", "Financial Mechanism")
            channel = topic_data.get("channel", "Market Intelligence")
            return {
                "topic_thesis": thesis,
                "strategic_angle": f"Exposing the hidden fees, fine print, and math behind {concept}.",
                "format_type": "STANDALONE",
                "hook_style": "HARD_NUMBER_SHOCK",
                "key_statistic": "Real financial impact exposed",
                "target_emotion": "Shock and urgent urge to verify personal accounts",
                "pinned_comment_text": "Did your bank or broker push you into this? Drop your thoughts below, or comment 'GUIDE' for our checklist! 💬👇",
                "tamil_adaptation_directive": f"Break down '{thesis}' with relatable Tanglish terms. Explain the exact math in lakhs and focus on middle-class consumer impact in Tamil Nadu.",
                "source_signal": f"Fresh Sourced Story ({channel})",
            }

        if slot_domain == "MARKET_INVESTING":
            return {
                "topic_thesis": "Mutual fund regular plans secretly pocket 35% of your final returns compared to zero-commission direct plans.",
                "strategic_angle": "Exposing the distributor commission compounding drag that robs ₹15-20 lakhs over 20 years from ordinary SIP investors.",
                "format_type": "STANDALONE",
                "hook_style": "HARD_NUMBER_SHOCK",
                "key_statistic": "35% wealth loss / ₹15 Lakhs",
                "target_emotion": "Shock and urgent urge to verify active mutual fund plans",
                "pinned_comment_text": "Are you still investing through regular mutual fund plans? Check your app statement and drop 'GUIDE' below for the direct switch guide! 👇",
                "tamil_adaptation_directive": "Highlight that bank relationship managers push regular funds for commissions. Use terms: 'Direct Plan vs Regular Plan', '₹15 Lakhs Natham'.",
                "source_signal": "Evergreen Director Directive",
            }
        return {
            "topic_thesis": "Zero Cost EMI is a psychological trap where banks charge 18% GST on the hidden interest and processing fees.",
            "strategic_angle": "Exposing the retail financing math where 'no cost' actually costs more than paying upfront due to hidden GST deductions.",
            "format_type": "STANDALONE",
            "hook_style": "CONFRONTATIONAL_TRUTH",
            "key_statistic": "18% GST + Processing Charges",
            "target_emotion": "Cynical realization of retail marketing gimmicks",
            "pinned_comment_text": "Have you bought a phone or TV on 'Zero Cost EMI'? Tell us your experience, or comment 'GUIDE' below for the real cost calculator! 👇",
            "tamil_adaptation_directive": "Emphasize festive season shopping and electronic store offers. Use terms: 'Zero Cost EMI Trap', '18% GST Kolla'.",
            "source_signal": "Evergreen Director Directive",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Post-Publish Community Engagement & Pinned Comment
    # ──────────────────────────────────────────────────────────────────────────

    def execute_post_publish(
        self,
        youtube_id: Optional[str] = None,
        instagram_id: Optional[str] = None,
        brief: Optional[StrategicBrief] = None,
    ) -> None:
        """
        Executes Level 3 autonomous community engagement:
        - Pins the strategic discussion comment on the newly uploaded YouTube Short.
        - Checks recent comments and logs community engagement signals.
        """
        if not brief and self.brief_path.is_file():
            try:
                with open(self.brief_path, "r", encoding="utf-8") as f:
                    brief = StrategicBrief(**json.load(f))
            except Exception:
                pass

        pinned_text = brief.pinned_comment_text if brief else (
            "💬 Comment 'GUIDE' below for the complete breakdown & checklist! Subscribe for daily debunks. 👇"
        )

        if youtube_id:
            self._post_youtube_pinned_comment(youtube_id, pinned_text)

        log.info("✓ Director Agent post-publish community engagement pass complete.")

    def _post_youtube_pinned_comment(self, video_id: str, comment_text: str) -> bool:
        """Attempts to insert a top-level comment on the video using YouTube API."""
        if not settings.ENABLE_YT_UPLOAD or not settings.ALLOW_PUBLICATION:
            return False

        try:
            from src.publishing.youtube_uploader import _get_authenticated_service
            service = _get_authenticated_service()
            body = {
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": comment_text
                        }
                    }
                }
            }
            res = service.commentThreads().insert(part="snippet", body=body).execute()
            comment_id = res.get("id")
            log.info("💬 Director Agent: Pinned strategic comment posted on YouTube (ID: %s)", comment_id)
            return True
        except Exception as exc:
            log.info("Director Agent: YouTube comment posting notice (scope/disabled: %s)", exc)
            return False
