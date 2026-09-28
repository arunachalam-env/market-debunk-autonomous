"""
src/agents/voice_agent.py

Phase 3 — Voice Synthesis (Google Cloud TTS)

Uses Google Cloud Text-to-Speech API for high-quality Neural voices.
Generates per-scene MP3 files and produces estimated word-level 
timestamp data for subtitle rendering.

Default voice: en-IN-Chirp3-HD-Fenrir (Look F — Dark Editorial)
"""
import html
import json
import re
import os
import time
import subprocess
from pathlib import Path

texttospeech = None

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="voice_synthesis")

DEFAULT_VOICE = settings.VOICE_NAME

def get_audio_duration(mp3_path: Path) -> float:
    """Returns the duration of an MP3 file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(mp3_path)],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception as exc:
        log.warning("ffprobe failed: %s", exc)
        return 5.0

def trim_audio_silence(input_path: Path, output_path: Path):
    """
    Trims leading silence and trailing silence cleanly, adding a natural 300ms (0.3s)
    breath pad so inter-scene transitions have comfortable breathing room without
    sounding rushed, robotic, or clipped at word endings.
    """
    try:
        af = (
            "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-45dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB,"
            "areverse,"
            "apad=pad_dur=0.04"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(input_path),
                "-af", af,
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(output_path)
            ],
            capture_output=True, check=True
        )
    except subprocess.CalledProcessError as e:
        log.error("FFmpeg silence removal failed: %s", e.stderr)
        # Fallback to the original file if trimming fails
        import shutil
        shutil.copy(input_path, output_path)


def normalize_english_for_tts(text: str) -> str:
    """
    Normalizes numbers, financial currency symbols, percentages, and acronyms
    so Google Neural TTS speaks with clear, natural cadence and zero phonetic glitches.
    """
    t = text.strip()
    t = re.sub(r"<[^>]+>", "", t)

    # 0. Clean numeric commas to prevent TTS pausing on commas (e.g. 50,000 -> 50000)
    t = re.sub(r"(\d+),(\d+)", r"\1\2", t)

    # 1. Currency with units (e.g. ₹1.2 Cr -> 1.2 crore rupees, $500 -> 500 dollars)
    t = re.sub(r"(?:₹|Rs\.?)\s*(\d+(?:\.\d+)?)\s*(?:Cr|Crores?|crores?)\b", r"\1 crore rupees", t)
    t = re.sub(r"(?:₹|Rs\.?)\s*(\d+(?:\.\d+)?)\s*(?:L|Lakhs?|lakhs?)\b", r"\1 lakh rupees", t)
    t = re.sub(r"(?:₹|Rs\.?)\s*(\d+(?:\.\d+)?)\s*[Kk]\b", r"\1 thousand rupees", t)
    t = re.sub(r"(?:₹|Rs\.?)\s*(\d+(?:\.\d+)?)", r"\1 rupees", t)
    t = t.replace("₹", " rupees ")

    t = re.sub(r"\$\s*(\d+(?:\.\d+)?)\s*(?:B|Billion|billion)\b", r"\1 billion dollars", t)
    t = re.sub(r"\$\s*(\d+(?:\.\d+)?)\s*(?:M|Million|million)\b", r"\1 million dollars", t)
    t = re.sub(r"\$\s*(\d+(?:\.\d+)?)", r"\1 dollars", t)

    # 2. Percentages & decimals (e.g. 0.4% -> zero point four percent, 6.8% -> 6.8 percent)
    t = re.sub(r"0\.(\d+)\s*%", r"zero point \1 percent", t)
    t = re.sub(r"(\d+)\.(\d+)\s*%", r"\1 point \2 percent", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1 percent", t)

    # 3. Financial acronyms & rapid phonetic pronunciations (zero awkward pauses)
    t = re.sub(r"\bUPI\b|\bupi\b", "You-Pee-Eye", t)
    t = re.sub(r"\bNPCI\b", "N P C I", t)
    t = re.sub(r"\bMDR\b", "M D R", t)
    t = re.sub(r"\bZero-MDR\b|\bzero-mdr\b", "Zero M D R", t)
    t = re.sub(r"\bPPI\b", "P P I", t)
    t = re.sub(r"\bIMPS\b", "I M P S", t)
    t = re.sub(r"\bNEFT\b", "N E F T", t)
    t = re.sub(r"\bRTGS\b", "R T G S", t)
    t = re.sub(r"\bKYC\b", "K Y C", t)
    t = re.sub(r"\bF&O\b|\bf&o\b", "F and O", t)
    t = re.sub(r"\bP/E\b|\bp/e\b", "P E", t)
    t = re.sub(r"\bFD\b", "F D", t)
    t = re.sub(r"\bFDs\b", "F Ds", t)
    t = re.sub(r"\bRD\b", "R D", t)
    t = re.sub(r"\bIPO\b", "I P O", t)
    t = re.sub(r"\bIPOs\b", "I P Os", t)
    t = re.sub(r"\bSIP\b", "S I P", t)
    t = re.sub(r"\bSIPs\b", "S I Ps", t)
    t = re.sub(r"\bEMI\b", "E M I", t)
    t = re.sub(r"\bEMIs\b", "E M Is", t)
    t = re.sub(r"\bGST\b", "G S T", t)
    t = re.sub(r"\bATM\b", "A T M", t)
    t = re.sub(r"\bAMC\b", "A M C", t)
    t = re.sub(r"\bRBI\b", "R B I", t)
    t = re.sub(r"\bSEBI\b", "SEBI", t)
    t = re.sub(r"\bROI\b", "R O I", t)
    t = re.sub(r"\bCAGR\b", "C A G R", t)
    t = re.sub(r"\bHDFC\b", "H D F C", t)
    t = re.sub(r"\bSBI\b", "S B I", t)
    t = re.sub(r"\bLIC\b", "L I C", t)
    t = re.sub(r"\bNAV\b", "N A V", t)
    t = re.sub(r"\bAUM\b", "A U M", t)
    t = re.sub(r"\bCEO\b", "C E O", t)
    t = re.sub(r"\bNIFTY\b", "Nifty", t)
    t = re.sub(r"\bSENSEX\b", "Sensex", t)
    t = re.sub(r"\bvs\.?\b", "versus", t, flags=re.IGNORECASE)

    # 5. Convert colons and semicolons to natural spoken pause markers (commas)
    t = re.sub(r"[:;]\s*", ", ", t)

    t = re.sub(r"\s+", " ", t).strip()
    return t


def _build_ssml(narration: str, scene_id: int = 1) -> str:
    """
    Build natural conversational SSML with controlled clause-level breath breaks:
    - 180ms breath pause at commas and em-dashes.
    - 280ms breath pause between sentences within the scene.
    - Normalizes currencies, percentages, and financial acronyms before synthesis.
    """
    norm_text = normalize_english_for_tts(narration)
    # Use quote=False so ' and " remain literal characters in XML text content,
    # preventing XML entity mangling (e.g., &#x27; or &quot;).
    escaped = html.escape(norm_text, quote=False)

    # Replace em-dashes and en-dashes with clause breath break
    escaped = re.sub(r"\s*[—–]\s*", ', <break time="180ms"/> ', escaped)
    # Micro-break after commas (protecting commas inside numbers like 50,000)
    escaped = re.sub(r",(?!\d)\s*", ', <break time="180ms"/> ', escaped)
    # Sentence boundary breath break if multiple sentences exist within scene
    escaped = re.sub(r"([.!?])\s+(?=[A-Z0-9])", r'\1 <break time="280ms"/> ', escaped)
    # Clean up duplicate break tags
    escaped = re.sub(r'(<break time="\d+ms"/>\s*)+', r'\1', escaped)

    rate_pct = int(settings.VOICE_SPEAKING_RATE * 100)
    rate = f"{rate_pct}%"
    # Chirp voices reject pitch parameter in prosody
    if "chirp" in settings.VOICE_NAME.lower():
        prosody_open = f'<prosody rate="{rate}">'
    else:
        pitch = f"{settings.VOICE_PITCH:+.1f}st"
        prosody_open = f'<prosody rate="{rate}" pitch="{pitch}">'
    ssml = f"<speak>{prosody_open}{escaped}</prosody></speak>"

    # Enforce max SSML length (Google limit ~5000 chars). If exceeded, fall back to plain escaped text.
    max_len = getattr(settings, "MAX_SSML_LENGTH", 4800)
    if len(ssml) > max_len:
        log.warning("SSML length %d exceeds max %d, falling back to plain text for scene %d", len(ssml), max_len, scene_id)
        return f"<speak>{escaped}</speak>"
    return ssml


def _synthesize_fish_audio(
    text: str,
    output_path: Path,
    voice_id: str,
    api_key: str,
    model_string: str = "s2.1-pro-free",
) -> bool:
    """Call Fish Audio S2.1 Pro API with exponential backoff on HTTP 429."""
    import requests
    import time
    import random

    if api_key:
        api_key = api_key.strip().replace('\ufeff', '').replace('\u200b', '')
        api_key = re.sub(r'[^\x20-\x7E]', '', api_key)
    if voice_id:
        voice_id = voice_id.strip().replace('\ufeff', '').replace('\u200b', '')
        voice_id = re.sub(r'[^\x20-\x7E]', '', voice_id)

    url = "https://api.fish.audio/v1/tts"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": model_string,
    }
    payload = {
        "text": text,
        "reference_id": voice_id,
        "format": "mp3",
        "normalize": True,
        "temperature": 0.85,
        "top_p": 0.85,
        "chunk_length": 200,
        "prosody": {
            "speed": 1.12,
            "volume": 0.0
        }
    }

    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=35)
            if res.status_code == 200 and len(res.content) > 1000:
                output_path.write_bytes(res.content)
                return True
            elif res.status_code == 429:
                wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                log.warning("Fish Audio 429 rate limit reached. Backing off for %.2fs (attempt %d/%d)...", wait_time, attempt, max_attempts)
                time.sleep(wait_time)
            else:
                log.warning("Fish Audio returned HTTP %d: %s", res.status_code, res.text[:200])
                time.sleep(2)
        except Exception as exc:
            log.warning("Fish Audio network attempt %d failed: %s", attempt, exc)
            time.sleep(2)

    return False


def _synthesize_elevenlabs(
    text: str,
    output_path: Path,
    api_key: str,
    voice_id: str = "21m00Tcm4TlvDq8ikWAM",
) -> bool:
    """Secondary fallback using ElevenLabs API with expressive voice settings."""
    import requests
    if not api_key:
        return False
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.85,
            "style": 0.45,
            "use_speaker_boost": True
        }
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=35)
        if res.status_code == 200 and len(res.content) > 1000:
            output_path.write_bytes(res.content)
            log.info("✓ Successfully synthesized with ElevenLabs fallback!")
            return True
        else:
            log.warning("ElevenLabs returned HTTP %d: %s", res.status_code, res.text[:200])
    except Exception as err:
        log.warning("ElevenLabs request failed: %s", err)
    return False


def synthesize_scene(
    scene_id: int,
    narration: str,
    audio_dir: Path,
    voice_name: str = DEFAULT_VOICE,
    speaker: str = "",
) -> dict:
    """Synthesize a single scene's narration using Fish Audio S2.1 Pro with ElevenLabs and Edge TTS fallbacks."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    
    raw_mp3_path = audio_dir / f"scene_{scene_id}_raw.mp3"
    mp3_path = audio_dir / f"scene_{scene_id}.mp3"
    timings_path = audio_dir / f"scene_{scene_id}_timings.json"

    api_key = getattr(settings, "FISH_AUDIO_API_KEY", "") or os.environ.get("FISH_AUDIO_API_KEY", "")
    voice_id = getattr(settings, "FISH_AUDIO_VOICE_ID", "") or os.environ.get("FISH_AUDIO_VOICE_ID", "d4db1b7753414bd29b8c7ef60e794a44")
    # Dialogue mode (owner-locked 2026-09-28): per-scene speaker -> voice routing.
    # ARUN keeps the owner's English clone; HER gets the dedicated female voice (julia).
    if (speaker or "").strip().upper() == "HER":
        voice_id = getattr(settings, "FISH_AUDIO_VOICE_ID_HER", "") or os.environ.get("FISH_AUDIO_VOICE_ID_HER", "eb81775a43bc469dafda02de59d13cb7")
    model_str = getattr(settings, "FISH_AUDIO_MODEL", "s2.1-pro-free")
    eleven_key = getattr(settings, "ELEVENLABS_API_KEY", "") or os.environ.get("ELEVENLABS_API_KEY", "")
    eleven_voice = getattr(settings, "ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

    clean_text = normalize_english_for_tts(narration)
    log.info("🎙️ Synthesizing scene %d with Fish Audio S2.1 Pro (Voice ID: %s, temp: 0.92)...", scene_id, voice_id)

    success = _synthesize_fish_audio(
        text=clean_text,
        output_path=raw_mp3_path,
        voice_id=voice_id,
        api_key=api_key,
        model_string=model_str,
    )

    if not success or not raw_mp3_path.exists():
        log.error("❌ Mandatory Fish Audio S2.1 Pro failed for scene %d.", scene_id)
        raise RuntimeError(f"Fish Audio voice synthesis is mandatory and failed for scene {scene_id}. Check API key and quota.")

    # Trim silence to ensure fluid pacing across scene cuts
    trim_audio_silence(raw_mp3_path, mp3_path)
    raw_mp3_path.unlink(missing_ok=True)
    
    duration = get_audio_duration(mp3_path)
    
    # Generate approximate word timings for subtitles based on character length
    words = narration.split()
    clean_words = ["".join(c for c in w if c.isalnum()) for w in words]
    total_chars = sum(len(w) for w in clean_words)
    
    word_timings = []
    current_time = 0.0
    
    for i, w in enumerate(words):
        c_len = max(len(clean_words[i]), 1)
        word_duration = duration * (c_len / max(total_chars, 1))
        
        word_timings.append({
            "word": w,
            "start": current_time,
            "end": current_time + word_duration
        })
        current_time += word_duration
        
    timings_path.write_text(json.dumps(word_timings, indent=2, ensure_ascii=False), encoding="utf-8")

    # Gentle inter-scene pacing throttle (400ms) to respect Fair Use limits
    time.sleep(0.4)

    return {
        "scene_id": scene_id,
        "mp3_path": str(mp3_path),
        "timings_path": str(timings_path),
        "duration": duration,
        "word_timings": word_timings,
    }

def synthesize_all_scenes(scenes: list[dict], audio_dir: Path, voice: str = DEFAULT_VOICE) -> list[dict]:
    """Synthesize voice for all scenes using Google TTS."""
    results = []

    for scene in scenes:
        sid = scene["scene_id"]
        narration = scene["narration"]

        try:
            result = synthesize_scene(sid, narration, audio_dir, voice, speaker=scene.get("speaker", ""))
            results.append(result)
            log.info(
                "✓ Scene %d synthesized | duration: %.1fs | words: %d",
                sid, result["duration"], len(result["word_timings"])
            )
        except Exception as exc:
            log.error("✗ Scene %d synthesis FAILED: %s", sid, exc)
            raise

    total_duration = sum(r["duration"] for r in results)
    log.info("All %d scenes synthesized | total audio: %.1fs", len(results), total_duration)

    target_duration = float(getattr(settings, "VIDEO_DURATION_TARGET", 25.0))
    max_duration_cap = float(getattr(settings, "MAX_VIDEO_DURATION", 45.0)) - 3.0  # ~42.0s

    if total_duration > max_duration_cap:
        speedup = min(1.15, max(1.02, total_duration / target_duration))
        log.info(
            "⏱️ Total voice duration (%.1fs) exceeds %.1fs cap. Applying automatic FFmpeg atempo speedup of %.3fx to reach ~%.1fs target...",
            total_duration, max_duration_cap, speedup, target_duration
        )
        for r in results:
            mp3_path = Path(r["mp3_path"])
            if mp3_path.exists():
                tmp_mp3 = mp3_path.with_name(f"{mp3_path.stem}_speed.mp3")
                cmd = [
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-af", f"atempo={speedup:.4f}",
                    "-c:a", "libmp3lame", "-b:a", "192k",
                    "-vn", str(tmp_mp3)
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and tmp_mp3.exists():
                    tmp_mp3.replace(mp3_path)
                    r["duration"] = get_audio_duration(mp3_path)
                else:
                    log.warning("FFmpeg atempo failed for %s: %s", mp3_path, res.stderr)
                    r["duration"] = round(r["duration"] / speedup, 2)
            else:
                r["duration"] = round(r["duration"] / speedup, 2)

            for wt in r.get("word_timings", []):
                if "start" in wt:
                    wt["start"] = round(wt["start"] / speedup, 3)
                if "end" in wt:
                    wt["end"] = round(wt["end"] / speedup, 3)

            timings_path = Path(r["timings_path"])
            if timings_path.exists():
                timings_path.write_text(
                    json.dumps(r["word_timings"], indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )

    min_duration_floor = float(getattr(settings, "MIN_VIDEO_DURATION", 15.0))
    if total_duration < min_duration_floor:
        slowdown = max(0.85, total_duration / 18.0)
        log.info(
            "⏱️ Total voice duration (%.1fs) is below %.1fs floor. Applying automatic FFmpeg atempo slowdown of %.3fx to reach ~18.0s target...",
            total_duration, min_duration_floor, slowdown
        )
        for r in results:
            mp3_path = Path(r["mp3_path"])
            if mp3_path.exists():
                tmp_mp3 = mp3_path.with_name(f"{mp3_path.stem}_slow.mp3")
                cmd = [
                    "ffmpeg", "-y", "-i", str(mp3_path),
                    "-af", f"atempo={slowdown:.4f}",
                    "-c:a", "libmp3lame", "-b:a", "192k",
                    "-vn", str(tmp_mp3)
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and tmp_mp3.exists():
                    tmp_mp3.replace(mp3_path)
                    r["duration"] = get_audio_duration(mp3_path)
                else:
                    r["duration"] = round(r["duration"] / slowdown, 2)
            else:
                r["duration"] = round(r["duration"] / slowdown, 2)

            for wt in r.get("word_timings", []):
                if "start" in wt:
                    wt["start"] = round(wt["start"] / slowdown, 3)
                if "end" in wt:
                    wt["end"] = round(wt["end"] / slowdown, 3)

            timings_path = Path(r["timings_path"])
            if timings_path.exists():
                timings_path.write_text(
                    json.dumps(r["word_timings"], indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )

    new_total = sum(r["duration"] for r in results)
    log.info("✅ Auto-compressed voice track duration: %.1fs -> %.1fs", total_duration, new_total)

    return results


def get_available_voices() -> list[str]:
    """Return configured Fish Audio voice and fallbacks."""
    voice_id = getattr(settings, "FISH_AUDIO_VOICE_ID", "d4db1b7753414bd29b8c7ef60e794a44")
    return [f"fish_audio:{voice_id}", "elevenlabs:21m00Tcm4TlvDq8ikWAM", "edge-tts:en-IN-PrabhatNeural"]
