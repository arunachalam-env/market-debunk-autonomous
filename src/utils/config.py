"""
src/utils/config.py
Centralised settings loader. Reads from .env file (local dev) or
environment variables (GitHub Actions secrets). Validates all required
keys at startup and raises clear errors if any are missing.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env if present (local dev). GitHub Actions sets env vars directly.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)


def _get(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    val = os.environ.get(key, default)
    if required and not val:
        raise EnvironmentError(
            f"[Config] Required environment variable '{key}' is missing.\n"
            f"  → Set it in your .env file (local) or GitHub Secrets (CI)."
        )
    return val


class Settings:
    """All pipeline settings, validated at import time."""

    # ── YouTube Data API ─────────────────────────────────────────
    YT_API_KEY: str = _get("YT_API_KEY", required=False) or ""

    # ── Script Generation (Gemma via Google AI Studio) ────────────
    GEMINI_SCRIPT_API_KEY: str = _get("GEMINI_SCRIPT_API_KEY", required=False) or ""
    GEMINI_API_KEY: str = _get("GEMINI_API_KEY", required=False) or ""
    GEMMA_FALLBACK_MODEL: str = _get("GEMMA_FALLBACK_MODEL", required=False) or "gemma-3-27b-it"

    # ── Gemini Live (Voice Synthesis) ─────────────────────────────
    GEMINI_LIVE_API_KEY: str = _get("GEMINI_LIVE_API_KEY", required=False) or ""

    # ── Gemini Image (Cloud GCP — image generation ONLY) ──────────
    GEMINI_IMAGE_API_KEY: str = _get("GEMINI_IMAGE_API_KEY", required=False) or ""

    # Groq Fallback
    GROQ_API_KEY: str = _get("GROQ_API_KEY", required=False) or ""
    GROQ_FALLBACK_MODEL: str = _get("GROQ_FALLBACK_MODEL", required=False) or "openai/gpt-oss-120b"

    # ── Transcript provider & Dedicated Market APIs ───────────────
    RAPIDAPI_KEY: str = _get("RAPIDAPI_KEY", required=False) or ""
    SERPAPI_KEY: str = _get("SERPAPI_KEY", required=False) or ""
    INDIAN_API_KEY: str = _get("INDIAN_API_KEY", default="sk-live-Ca1EJj4XFo61nRpchb93tlGrs0IyVEC5cl4A6iF5") or "sk-live-Ca1EJj4XFo61nRpchb93tlGrs0IyVEC5cl4A6iF5"
    MARKETAUX_API_TOKEN: str = _get("MARKETAUX_API_TOKEN", default="bZ1PVR803PweIGinKuMa1r6Zk4kPn4v8xikQvUkC") or "bZ1PVR803PweIGinKuMa1r6Zk4kPn4v8xikQvUkC"

    # ── Pexels (Background Footage) ───────────────────────────────
    PEXELS_API_KEY: str = _get("PEXELS_API_KEY", required=False) or ""

    # ── YouTube Publishing ─────────────────────────────────────────
    ENABLE_YT_UPLOAD: bool = (_get("ENABLE_YT_UPLOAD", default="true") or "true").lower() == "true"
    ALLOW_PUBLICATION: bool = (_get("ALLOW_PUBLICATION", default="true") or "true").lower() == "true"
    YT_CLIENT_ID: str = _get("YT_CLIENT_ID", default="") or ""
    YT_CLIENT_SECRET: str = _get("YT_CLIENT_SECRET", default="") or ""
    YT_REFRESH_TOKEN: str = _get("YT_REFRESH_TOKEN", default="") or ""

    # ── Telegram Notifications ─────────────────────────────────────
    ENABLE_TELEGRAM: bool = (_get("ENABLE_TELEGRAM", default="true") or "true").lower() == "true"
    TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN", default="") or ""
    TELEGRAM_CHAT_ID: str = _get("TELEGRAM_CHAT_ID", default="") or ""

    # ── Instagram & Facebook Graph API publishing ────────────────
    ENABLE_INSTAGRAM: bool = (_get("ENABLE_INSTAGRAM", default="true") or "true").lower() == "true"
    INSTAGRAM_ACCESS_TOKEN: str = _get("INSTAGRAM_ACCESS_TOKEN") or _get("META_ACCESS_TOKEN") or ""
    INSTAGRAM_USER_ID: str = _get("INSTAGRAM_USER_ID", default="") or ""
    INSTAGRAM_VIDEO_URL: str = _get("INSTAGRAM_VIDEO_URL", default="") or ""
    INSTAGRAM_GRAPH_VERSION: str = _get("INSTAGRAM_GRAPH_VERSION", default="v23.0") or "v23.0"
    ENABLE_FACEBOOK: bool = (_get("ENABLE_FACEBOOK", default="true") or "true").lower() == "true"
    FACEBOOK_PAGE_ID: str = _get("FACEBOOK_PAGE_ID", default="") or ""
    FACEBOOK_ACCESS_TOKEN: str = _get("FACEBOOK_ACCESS_TOKEN") or _get("INSTAGRAM_ACCESS_TOKEN") or _get("META_ACCESS_TOKEN") or ""

    # ── Video Settings ─────────────────────────────────────────────
    VIDEO_WIDTH: int = int(_get("VIDEO_WIDTH", default="1080"))
    VIDEO_HEIGHT: int = int(_get("VIDEO_HEIGHT", default="1920"))
    VIDEO_FPS: int = int(_get("VIDEO_FPS", default="30"))
    VIDEO_DURATION_TARGET: int = int(_get("VIDEO_DURATION_TARGET", default="80"))
    # -- Story Mode (Arun storybook format) ----------------------------------
    # When enabled, the pipeline produces 60-85s illustrated story videos
    # (10-12 scenes, hard max 90s) instead of 24s stock-footage Shorts.
    STORY_MODE: bool = (_get("STORY_MODE", default="true") or "true").lower() == "true"
    # Two-character 4pm dialogue format (owner-locked roles 2026-09-28, 14-day sprint):
    # she asks (curious friend, viewer's voice), Arun answers (expert). Off until name/voice picks land.
    DIALOGUE_MODE: bool = (_get("DIALOGUE_MODE", default="false") or "false").lower() == "true"
    HER_NAME: str = _get("HER_NAME", default="Liya")  # owner-locked 2026-09-28: Liya, face option C
    STORY_IMAGE_MODEL: str = _get("STORY_IMAGE_MODEL", default="gemini-2.5-flash-image") or "gemini-2.5-flash-image"
    MIN_VIDEO_DURATION: float = float(_get("MIN_VIDEO_DURATION", default=("45.0" if STORY_MODE else "15.0")))
    MAX_VIDEO_DURATION: float = float(_get("MAX_VIDEO_DURATION", default=("93.0" if (STORY_MODE or DIALOGUE_MODE) else "45.0")))
    VISUAL_GENERATION_DELAY_SECONDS: float = float(_get("VISUAL_GENERATION_DELAY_SECONDS", default="10"))
    BGM_VOLUME_DB: float = float(_get("BGM_VOLUME_DB", default="-5.5"))
    BGM_MIX_RETRIES: int = int(_get("BGM_MIX_RETRIES", default="3"))
    BGM_MIX_REQUIRED: bool = (_get("BGM_MIX_REQUIRED", default="true") or "true").lower() == "true"
    BGM_MIN_BYTES: int = int(_get("BGM_MIN_BYTES", default="1000000"))

    # ── Voice Settings ─────────────────────────────────────────────
    # Primary: Fish Audio S2.1 Pro with custom human cloned voice
    TTS_PROVIDER: str = _get("TTS_PROVIDER", default="fish_audio") or "fish_audio"
    FISH_AUDIO_API_KEY: str = _get("FISH_AUDIO_API_KEY", default="") or ""
    FISH_AUDIO_VOICE_ID: str = _get("FISH_AUDIO_VOICE_ID", default="d8a1340984ee4b63ad1ffae27a6a4339") or "d8a1340984ee4b63ad1ffae27a6a4339"  # Arun = Fish "ELITE" for English AND Tamil, owner ear-picked 2026-09-28; duo + morning solo. Tamil TTS input is ALWAYS romanized Tanglish (English letters), never native Tamil script (owner rule 7:42am)
    FISH_AUDIO_VOICE_ID_HER: str = _get("FISH_AUDIO_VOICE_ID_HER", default="933563129e564b19a115bedd57b7406a") or "933563129e564b19a115bedd57b7406a"  # Liya = Fish "Sarah", owner ear-picked 2026-09-28
    FISH_AUDIO_SPEED_HER: str = _get("FISH_AUDIO_SPEED_HER", default="1.2") or "1.2"  # Sarah at 1.2x, owner ear-picked ("quite slow" at 1.0x)
    FISH_AUDIO_VOLUME_HER: str = _get("FISH_AUDIO_VOLUME_HER", default="3.0") or "3.0"  # dB boost; owner 7:43am "increase the volume" - also duck BGM lower under her lines in the mix
    FISH_AUDIO_MODEL: str = _get("FISH_AUDIO_MODEL", default="s2.1-pro-free") or "s2.1-pro-free"
    
    # Secondary Fallback: ElevenLabs TTS
    ELEVENLABS_API_KEY: str = _get("ELEVENLABS_API_KEY", default="") or ""
    ELEVENLABS_VOICE_ID: str = _get("ELEVENLABS_VOICE_ID", default="21m00Tcm4TlvDq8ikWAM") or "21m00Tcm4TlvDq8ikWAM"
    
    # Fallback / Secondary: Google Cloud TTS
    VOICE_NAME: str = _get("VOICE_NAME", default="en-IN-Chirp3-HD-Fenrir") or "en-IN-Chirp3-HD-Fenrir"
    VOICE_SPEAKING_RATE: float = float(_get("VOICE_SPEAKING_RATE", default="1.10"))
    VOICE_PITCH: float = float(_get("VOICE_PITCH", default="0.0"))
    MAX_SSML_LENGTH: int = int(_get("MAX_SSML_LENGTH", default="4800"))

    # ── Pre-publication Fact-Check Gate ────────────────────────────
    FACT_CHECK_ENABLED: bool = (_get("FACT_CHECK_ENABLED", default="true") or "true").lower() == "true"
    # When true, a failed/unrunnable check BLOCKS publication (fail-closed).
    FACT_CHECK_REQUIRED: bool = (_get("FACT_CHECK_REQUIRED", default="true") or "true").lower() == "true"
    FACT_CHECK_MODEL: str = _get("FACT_CHECK_MODEL", default="gemini-3.8-flash") or "gemini-3.8-flash"

    # ── Deduplication ─────────────────────────────────────────────
    DEDUP_THRESHOLD: float = float(_get("DEDUP_THRESHOLD", default="0.75"))
    DEDUP_WINDOW_DAYS: int = int(_get("DEDUP_WINDOW_DAYS", default="30"))

    # ── Paths ─────────────────────────────────────────────────────
    ROOT_DIR: Path = Path(__file__).resolve().parents[2]
    DATA_DIR: Path = ROOT_DIR / "data"
    OUTPUT_DIR: Path = ROOT_DIR / "output"
    ASSETS_DIR: Path = ROOT_DIR / "assets"
    BGM_PATH: Path = ASSETS_DIR / "bgm" / "background.mp3"
    BRAND_MARK_PATH: Path = Path(_get("BRAND_MARK_PATH", default=str(ASSETS_DIR / "brand_protection.png")))
    BRAND_MARK_WIDTH: int = int(_get("BRAND_MARK_WIDTH", default="150"))
    BRAND_MARK_PADDING: int = int(_get("BRAND_MARK_PADDING", default="30"))

    # ── Subtitle Style ────────────────────────────────────────────
    SUBTITLE_FONT: str = "Bebas Neue"
    SUBTITLE_FONT_SIZE: int = 112
    SUBTITLE_PRIMARY_COLOR: str = "&H00FFFFFF"   # white
    SUBTITLE_OUTLINE_COLOR: str = "&H00000000"   # black
    SUBTITLE_MARGIN_V: int = 470                  # safe above Shorts handle/description UI
    SUBTITLE_MARGIN_H: int = 108                  # 10% per side → 80% text width on 1080px canvas

    # ── YouTube Channel Registry (day-indexed, 0=Monday) ─────────
    CHANNEL_REGISTRY: list[str] = [
        "MONEY PECHU",           # Monday
        "PR SUNDAR",             # Tuesday
        "MONEY PURSE",           # Wednesday
        "TRADE ACHIEVERS",       # Thursday
        "MARKET DRIVER",         # Friday
        "TAMIL NIFTY ANALYSIS",  # Saturday
        "ZERO1 BY ZERODHA",      # Sunday
    ]


settings = Settings()


def validate_for_run() -> list[str]:
    """
    Returns a list of warnings about missing optional keys.
    Raises EnvironmentError for hard-required keys.
    """
    warnings: list[str] = []

    if not settings.YT_API_KEY:
        warnings.append("YT_API_KEY missing — will use RSS feed fallback only")
    if not settings.GROQ_API_KEY:
        raise EnvironmentError("GROQ_API_KEY is required for script generation")
    if not settings.PEXELS_API_KEY:
        warnings.append("PEXELS_API_KEY missing — will use Pollinations image fallback")
    if not settings.RAPIDAPI_KEY:
        warnings.append("RAPIDAPI_KEY missing — transcript discovery will use yt-dlp fallback")
    if not settings.SERPAPI_KEY:
        warnings.append("SERPAPI_KEY missing — market-news fallback disabled")
    if settings.ENABLE_INSTAGRAM and not all((settings.INSTAGRAM_ACCESS_TOKEN, settings.INSTAGRAM_USER_ID, settings.INSTAGRAM_VIDEO_URL)):
        warnings.append("Instagram enabled but credentials/video URL are incomplete — Instagram upload will be skipped")
    if not settings.GEMINI_IMAGE_API_KEY:
        warnings.append("GEMINI_IMAGE_API_KEY missing — AI image generation disabled")
    if not settings.GEMINI_LIVE_API_KEY:
        warnings.append("GEMINI_LIVE_API_KEY missing — voice will use gTTS fallback")

    # Ensure output dir exists
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    return warnings
