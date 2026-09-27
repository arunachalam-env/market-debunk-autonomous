"""
src/agents/distribution_seo_agent.py

Autonomous Distribution & Multi-Platform SEO Agent for Market Debunk.
Analyzes the video script, core thesis, and spoken narration to produce
platform-native metadata engineered for 2026 short-form algorithms:

1. YouTube Shorts: Search intent matching, 150-char description snippet, 3 key takeaways, 8-12 search tags, pinned comment prompt.
2. Instagram Reels: Visible hook (<120 chars), micro-value copy, DM triggers, save & share hooks (the #1 ranking signal), 4 niche hashtags.
3. Facebook Reels: Conversational storytelling narrative, everyday investor dilemma, open-ended debate question, 3-4 topic tags.
4. Telegram VIP Channel: High-signal editorial layout, bold alert, Myth vs Reality bullets, golden investor rule, zero spam tags.
"""
from __future__ import annotations

import json
import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.youtube_titles import (
    clean_word_truncate,
    format_high_reach_title,
    normalize_youtube_title,
    resolve_high_reach_keyword,
)

log = get_logger(__name__, phase="seo_distribution")


def _cap_hashtags(tags: list[str], cap: int, platform: str) -> list[str]:
    """LLM outputs routinely ignore the 'exactly N hashtags' instruction; cap
    deterministically. Hashtag stuffing (>5) is near-decorative on Shorts and
    reads as spam on Reels."""
    if len(tags) > cap:
        log.warning("%s hashtags over cap (%d > %d); trimming to the first %d.", platform, len(tags), cap, cap)
        return tags[:cap]
    return tags


class YouTubeDistribution(BaseModel):
    title: str = Field(description="Search-first, high-reach keyword-frontloaded YouTube Short title. Max 55 chars. Ends with #Shorts.")
    snippet: str = Field(description="First 150 characters of description. Natural SEO search query paragraph.")
    takeaways: list[str] = Field(description="Exactly 3 concise bullet points debunking the myth/warning.")
    hashtags: list[str] = Field(description="Exactly 3-5 high volume search hashtags.")
    search_tags: list[str] = Field(description="8-12 search keyword phrases for backend tags.")
    pinned_comment: str = Field(description="Provocative question to seed immediate comment velocity.")

    @field_validator("hashtags")
    @classmethod
    def _cap_yt_hashtags(cls, v):
        return _cap_hashtags(v, 5, "YouTube")


class InstagramDistribution(BaseModel):
    first_line_hook: str = Field(description="Max 120 chars. Scroll-stopping hook visible BEFORE the '...more' fold.")
    body_copy: str = Field(description="2-3 short micro-value sentences explaining the financial reality.")
    share_save_cta: str = Field(description="Explicit save/share trigger (e.g. '📌 Save this before your next SIP / 📩 Send to a friend').")
    comment_trigger: str = Field(description="Comment CTA trigger (e.g. '💬 Comment GUIDE below for the full breakdown').")
    hashtags: list[str] = Field(description="Exactly 4 hyper-targeted niche hashtags.")

    @field_validator("hashtags")
    @classmethod
    def _cap_ig_hashtags(cls, v):
        return _cap_hashtags(v, 4, "Instagram")


class FacebookDistribution(BaseModel):
    story_hook: str = Field(description="Conversational storytelling narrative opening (everyday investor dilemma).")
    narrative_body: str = Field(description="100-180 words relatable explanation of what really happens.")
    discussion_question: str = Field(description="Open-ended debate question that triggers comments.")
    topic_tags: list[str] = Field(description="3-4 topic hashtags.")

    @field_validator("topic_tags")
    @classmethod
    def _cap_fb_tags(cls, v):
        return _cap_hashtags(v, 4, "Facebook")


class TelegramDistribution(BaseModel):
    alert_headline: str = Field(description="Bold headline with emoji alert.")
    myth_point: str = Field(description="The common misconception retail investors fall for.")
    reality_point: str = Field(description="The cold, hard truth with numbers or institutional reality.")
    golden_rule: str = Field(description="The 1 actionable rule to protect money.")
    discussion_prompt: str = Field(description="Prompt to discuss in the community group.")


class PlatformDistributionPackage(BaseModel):
    youtube: YouTubeDistribution
    instagram: InstagramDistribution
    facebook: FacebookDistribution
    telegram: TelegramDistribution

    def get_youtube_description(self) -> str:
        """Compose rich YouTube description with SEO snippet and chapters."""
        takeaways_formatted = "\n".join(f"• {t}" for t in self.youtube.takeaways)
        tags_line = " ".join(self.youtube.hashtags)
        return (
            f"{self.youtube.snippet}\n\n"
            f"KEY TAKEAWAYS:\n"
            f"{takeaways_formatted}\n\n"
            f"📌 DISCLAIMER: Educational analysis only. We expose structural market realities.\n\n"
            f"{tags_line}\n\n"
            "🎵 Music: Kevin MacLeod (incompetech.com) - \"Dark Times\" / \"Night Vigil\" / \"Crypto\". "
            "Licensed under Creative Commons: By Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/)"
        )

    def get_instagram_caption(self) -> str:
        tags_line = " ".join(self.instagram.hashtags)
        return (
            f"{self.instagram.first_line_hook}\n\n"
            f"{self.instagram.body_copy}\n\n"
            f"{self.instagram.share_save_cta}\n\n"
            f"{self.instagram.comment_trigger}\n\n"
            f"{tags_line}"
        )

    def get_facebook_caption(self) -> str:
        tags_line = " ".join(self.facebook.topic_tags)
        return (
            f"{self.facebook.story_hook}\n\n"
            f"{self.facebook.narrative_body}\n\n"
            f"👉 {self.facebook.discussion_question}\n\n"
            f"{tags_line}"
        )

    def get_telegram_post(self, video_link: Optional[str] = None) -> str:
        link_line = f"\n\n🎬 Watch Video: {video_link}" if video_link else ""
        return (
            f"🚨 *MARKET DEBUNK ALERT*\n"
            f"*{self.telegram.alert_headline}*\n\n"
            f"❌ *The Myth:* {self.telegram.myth_point}\n"
            f"✅ *The Reality:* {self.telegram.reality_point}\n\n"
            f"💡 *Key Rule:* _{self.telegram.golden_rule}_\n\n"
            f"💬 {self.telegram.discussion_prompt}"
            f"{link_line}"
        )[:3900]


import random

_SAFE_FALLBACK_TITLES = [
    "Options Trading Loss: SEBI 90% Reality #Shorts",
    "Zero Cost EMI Trap: 18% Hidden GST Exposed #Shorts",
    "Mutual Fund SIP: Regular vs Direct ₹15L Loss #Shorts",
    "Credit Card Trap: Minimum Due Destroys CIBIL #Shorts",
    "Stock Market Beginners: 3 Fatal Mistakes #Shorts",
    "Fixed Deposit vs Mutual Fund: Real 7% Return #Shorts",
    "SME IPO Allotment: The 90% Listing Trap #Shorts",
    "Intraday Trading: The Hidden Brokerage Trap #Shorts",
]

_SEO_SYSTEM_PROMPT = """You are the Chief SEO & Social Distribution Strategist for 'Market Debunk'.
Your sole task is to generate platform-specialized copy that maximizes algorithmic distribution across:
1. YouTube Shorts (High-reach search keyword-frontloaded title under 55 chars ending in #Shorts, search-intent alignment, retention snippet)
2. Instagram Reels (First-line hook before '...more' truncation at 80 chars, DM save & share triggers, exactly 4 niche hashtags)
3. Facebook Reels (Relatable conversational storytelling, everyday investor dilemma, comment debate question)
4. Telegram VIP Channel (Clean editorial markdown, Myth vs Reality, Golden Rule, zero hashtag spam)

RULES:
- Never use generic placeholder text. Use actual financial context from the provided thesis and script.
- YouTube title MUST front-load the HIGH-VOLUME SEARCH KEYWORD in the first 25 characters, followed by a colon and the specific high-stakes punch/number/truth, ending in #Shorts.
  * FORMAT: [High-Reach Search Keyword]: [Punch/Number/Fact] #Shorts
  * ALLOWED EXAMPLES:
    - "Options Trading Loss: SEBI 90% Reality #Shorts"
    - "Zero Cost EMI Trap: 18% Hidden GST Exposed #Shorts"
    - "Mutual Fund SIP: Regular vs Direct ₹15L Loss #Shorts"
    - "Credit Card Trap: Minimum Due Destroys CIBIL #Shorts"
    - "Fixed Deposit vs Mutual Fund: Real 7% Return #Shorts"
  * STRICTLY BANNED: Vague storytelling, rhetorical questions, and abstract clickbait (e.g. BANNED: "Why You Lost Money", "What Nobody Tells You", "The Brutal Truth", "The Silent Killer", "Is Your Portfolio Hiding This?").
  * Total length MUST be strictly between 42 and 55 characters including #Shorts. Never cut words in half.
- Instagram caption must NOT have 20-30 spam hashtags. Use strictly 4 niche hashtags.
- Output MUST be strictly valid JSON matching the requested schema.
"""


class DistributionSEOAgent:
    """Agent responsible for multi-platform SEO and social distribution copy."""

    def __init__(self):
        self.gemini_key = settings.GEMINI_SCRIPT_API_KEY or settings.GEMINI_API_KEY
        self.groq_key = settings.GROQ_API_KEY

    def generate_package(
        self,
        thesis: str,
        script_dict: dict,
        topic_data: Optional[dict] = None,
    ) -> PlatformDistributionPackage:
        """Generate optimized multi-platform distribution package."""
        title = script_dict.get("title", "")
        scenes = script_dict.get("scenes", [])
        narration_full = " ".join(s.get("narration", "") for s in scenes)
        clean_thesis = thesis.strip()

        user_prompt = f"""VIDEO CONTEXT:
Thesis: {clean_thesis}
Working Title: {title}
Full Narration: {narration_full}

Generate the complete multi-platform SEO package as JSON matching the schema."""

        # 1. Try Gemini API with multi-key rotation and active models
        from src.agents.script_agent import _get_api_clients
        gemini_clients = _get_api_clients()
        last_gemini_err = None

        if gemini_clients:
            from google.genai import types

            models_priority = [
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.1-flash-lite",
                "gemini-flash-lite-latest",
            ]
            for model_name in models_priority:
                for client in gemini_clients:
                    try:
                        log.info("Calling Gemini (%s) for Multi-Platform Distribution SEO Package...", model_name)
                        response = client.models.generate_content(
                            model=model_name,
                            contents=user_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=_SEO_SYSTEM_PROMPT,
                                response_mime_type="application/json",
                                response_schema=PlatformDistributionPackage,
                                temperature=0.6,
                            ),
                        )
                        if response.text:
                            raw_json = response.text.strip()
                            if "```" in raw_json:
                                raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, flags=re.MULTILINE)
                                raw_json = re.sub(r"\s*```\s*$", "", raw_json, flags=re.MULTILINE)
                            data = json.loads(raw_json)
                            pkg = PlatformDistributionPackage.model_validate(data)
                            pkg.youtube.title = normalize_youtube_title(pkg.youtube.title)
                            log.info("✓ Gemini (%s) generated Multi-Platform SEO Package successfully.", model_name)
                            return pkg
                    except Exception as e:
                        last_gemini_err = e
                        log.warning("Gemini (%s) SEO generation failed (%s); trying next model...", model_name, e)

        if last_gemini_err:
            log.warning("Gemini SEO generation failed (%s); trying Groq fallback", last_gemini_err)
        elif not gemini_clients:
            log.warning("No Gemini API clients available; trying Groq fallback")

        # 2. Try Groq API
        groq_key = self.groq_key or os.environ.get("GROQ_API_KEY", "").strip()
        if groq_key:
            try:
                log.info("Calling Groq for Multi-Platform Distribution SEO Package...")
                from groq import Groq

                groq_client = Groq(api_key=groq_key)
                schema_json = json.dumps(PlatformDistributionPackage.model_json_schema(), indent=2)
                groq_prompt = f"{_SEO_SYSTEM_PROMPT}\n\nSchema:\n{schema_json}\n\nUser:\n{user_prompt}\n\nReturn JSON ONLY."
                resp = groq_client.chat.completions.create(
                    model=settings.GROQ_FALLBACK_MODEL,
                    messages=[{"role": "user", "content": groq_prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.5,
                )
                content = resp.choices[0].message.content
                if content:
                    data = json.loads(content)
                    pkg = PlatformDistributionPackage.model_validate(data)
                    pkg.youtube.title = normalize_youtube_title(pkg.youtube.title)
                    log.info("✓ Groq generated Multi-Platform SEO Package successfully.")
                    return pkg
            except Exception as e:
                log.warning("Groq SEO generation failed (%s); using deterministic generator", e)
        else:
            log.info("Groq API key not set; skipping Groq tier.")

        # 3. Deterministic Emergency Fallback
        log.warning("Falling back to unbreakable deterministic SEO Distribution Package...")
        return self._generate_fallback(thesis, script_dict)

    def _generate_fallback(self, thesis: str, script_dict: dict) -> PlatformDistributionPackage:
        clean_thesis = re.sub(r'[^a-zA-Z0-9\s]', '', thesis or "Market Truth")
        words = [w for w in clean_thesis.split() if len(w) > 2]
        hook_phrase = " ".join(words[:4]).title() if words else "Stock Market Truth"

        # Prioritize clean keyword-frontloaded title; else use curated safe template
        script_title = (script_dict.get("title") or "").replace("#Shorts", "").strip()
        keyword = resolve_high_reach_keyword(f"{thesis} {script_title}")
        if script_title and len(script_title) >= 10:
            yt_title = format_high_reach_title(keyword, script_title, max_length=55)
        else:
            yt_title = normalize_youtube_title(random.choice(_SAFE_FALLBACK_TITLES), max_length=55)

        return PlatformDistributionPackage(
            youtube=YouTubeDistribution(
                title=yt_title,
                snippet=f"Think {hook_phrase} is safe? Watch the real numbers institutions don't want retail traders to see.",
                takeaways=[
                    f"Why retail investors miscalculate {hook_phrase}.",
                    "The quiet fee and spread structure eroding gains.",
                    "How smart money repositions before the public notices.",
                ],
                hashtags=["#StockMarket", "#Investing", "#Nifty", "#Shorts"],
                search_tags=[
                    "stock market india",
                    "nifty 50 analysis",
                    "mutual funds vs stocks",
                    "investing mistakes",
                    "market debunk",
                ],
                pinned_comment="Have you noticed this happening in your portfolio? Tell us your experience below 👇",
            ),
            instagram=InstagramDistribution(
                first_line_hook=f"🚨 The dirty secret behind {hook_phrase} nobody tells you...",
                body_copy="Most retail traders think they are playing it safe, but the hidden mathematics tell a completely different story. Here is what actually happens behind the scenes.",
                share_save_cta="📌 Save this Reel before your next trade | 📩 Share with a friend who needs to see this.",
                comment_trigger="💬 Comment 'GUIDE' below to get our complete risk checklist sent straight to your DMs!",
                hashtags=["#IndianStockMarket", "#FinanceIndia", "#SmartInvesting", "#WealthCreation"],
            ),
            facebook=FacebookDistribution(
                story_hook=f"You thought {hook_phrase} was your safest financial move. We looked at the numbers, and the reality is shocking.",
                narrative_body="Thousands of retail investors fall into this exact trap every month because financial headlines make it look completely harmless. But when you look past the marketing, the hidden deductions and opportunity costs quietly drain your profits over time.",
                discussion_question="Would you keep your money here or rotate into better assets? Drop your thoughts below 👇",
                topic_tags=["#PersonalFinance", "#IndianInvestors", "#SmartMoney"],
            ),
            telegram=TelegramDistribution(
                alert_headline=f"{hook_phrase}: What They Won't Tell You",
                myth_point="Assuming high returns are guaranteed without understanding the downside mechanics.",
                reality_point="Institutions quietly hedge and capture spreads while retail money takes all the uncompensated volatility.",
                golden_rule="Never put capital into an asset without auditing the full fee and liquidity structure first.",
                discussion_prompt="Discuss your trading experience in our community group!",
            ),
        )

    def post_process(
        self,
        pkg: PlatformDistributionPackage,
        thesis: str,
        script_dict: Optional[dict] = None,
    ) -> PlatformDistributionPackage:
        """
        Run 3 SEO Super Subagents sequentially to enhance the base Gemini package.

        Each subagent independently researches live trending data via RapidAPI
        and rewrites platform-specific metadata for max algorithmic distribution.

        Order: YouTubeSEOAgent → InstagramSEOAgent → FacebookSEOAgent
        All subagents are non-blocking — failures silently pass through.
        """
        from src.agents.seo_subagents import YouTubeSEOAgent, InstagramSEOAgent, FacebookSEOAgent

        gemini_key = self.gemini_key or ""

        log.info("=" * 60)
        log.info("  SEO SUPER SUBAGENTS: Starting RapidAPI research pass...")
        log.info("=" * 60)

        # Subagent 1: YouTube
        try:
            yt_agent = YouTubeSEOAgent(gemini_key=gemini_key)
            yt_agent.enhance(pkg, thesis)
        except Exception as exc:
            log.warning("YouTubeSEOAgent failed (non-blocking): %s", exc)

        # Subagent 2: Instagram
        try:
            ig_agent = InstagramSEOAgent(gemini_key=gemini_key)
            ig_agent.enhance(pkg, thesis)
        except Exception as exc:
            log.warning("InstagramSEOAgent failed (non-blocking): %s", exc)

        # Subagent 3: Facebook
        try:
            fb_agent = FacebookSEOAgent(gemini_key=gemini_key)
            fb_agent.enhance(pkg, thesis)
        except Exception as exc:
            log.warning("FacebookSEOAgent failed (non-blocking): %s", exc)

        log.info("✅ SEO SUPER SUBAGENTS: All 3 platforms enhanced.")
        return pkg
