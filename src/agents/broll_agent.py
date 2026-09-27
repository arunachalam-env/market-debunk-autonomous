"""
src/agents/broll_agent.py

Dedicated B-Roll Curation & Freshness Agent.

Responsibilities:
1. Dynamic, context-tailored search query generation based on scene narration.
2. Pexels Video API integration with multi-result sampling (requesting 20 candidates,
   filtering strictly vertical 9:16 files, and sampling from top fresh matches).
3. Persistent deduplication via `data/used_broll.json` to prevent clips from ever repeating.
4. Clean fallbacks when stock video candidates are exhausted.
"""
from __future__ import annotations

import json
import random
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="broll_curation")

_BROLL_TRACKING_PATH = settings.DATA_DIR / "used_broll.json"
_EXPIRATION_DAYS = 30


# ──────────────────────────────────────────────────────────────────────────────
#  Persistent Deduplication Buffer
# ──────────────────────────────────────────────────────────────────────────────

def load_used_broll() -> dict[str, str]:
    """Load persistent map of { video_id_str: iso_timestamp }."""
    if not _BROLL_TRACKING_PATH.exists():
        return {}
    try:
        with open(_BROLL_TRACKING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log.warning("Could not read used_broll.json (%s), starting fresh.", exc)
    return {}


def save_used_broll(buffer: dict[str, str]) -> None:
    """Save persistent map of used video IDs."""
    _BROLL_TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_BROLL_TRACKING_PATH, "w", encoding="utf-8") as f:
            json.dump(buffer, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        log.error("Failed saving used_broll.json: %s", exc)


def evict_old_broll(buffer: dict[str, str]) -> dict[str, str]:
    """Remove records older than 30 days to keep the tracking file compact."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_EXPIRATION_DAYS)
    cleaned = {}
    for vid_id, ts_str in buffer.items():
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                cleaned[vid_id] = ts_str
        except Exception:
            cleaned[vid_id] = ts_str
    return cleaned


def record_used_video(video_id: int | str) -> None:
    """Record a video ID to the persistent deduplication buffer."""
    buf = load_used_broll()
    buf = evict_old_broll(buf)
    buf[str(video_id)] = datetime.now(timezone.utc).isoformat()
    save_used_broll(buf)


def is_broll_used_previously(video_id: int | str) -> bool:
    """Check whether a video ID has been used in previous runs."""
    buf = load_used_broll()
    return str(video_id) in buf


# ──────────────────────────────────────────────────────────────────────────────
#  Dynamic Query Generator
# ──────────────────────────────────────────────────────────────────────────────

def generate_scene_queries(scene: dict, story_seed: Optional[dict] = None) -> list[str]:
    """
    Generate 2-4 distinct, high-intent English search phrases for vertical stock video.
    Derives phrases from the scene's narration rather than relying on static prompt examples.
    """
    queries: list[str] = []
    
    # 1. Existing broll_keyword or broll_keywords if provided
    raw_kw = scene.get("broll_keyword", "").strip()
    if raw_kw:
        queries.append(raw_kw)

    raw_kws = scene.get("broll_keywords", [])
    if isinstance(raw_kws, list):
        for kw in raw_kws:
            if isinstance(kw, str) and kw.strip() and kw.strip() not in queries:
                queries.append(kw.strip())

    # 2. Extract key action/subject nouns from scene narration
    narration = scene.get("narration", "")
    clean_narration = re.sub(r"[^\w\s]", "", narration.lower())
    words = clean_narration.split()

    finance_triggers = {
        "tax": ["tax document paperwork", "calculating tax return"],
        "gst": ["tax invoice receipt", "credit card payment terminal"],
        "atm": ["atm cash withdrawal", "atm screen keypad"],
        "emi": ["online shopping checkout", "credit card swipe machine"],
        "loan": ["bank contract signing", "meeting bank officer"],
        "interest": ["interest rate chart", "counting bank notes"],
        "mutual fund": ["investment portfolio screen", "financial charts smartphone"],
        "fund": ["stock market green red", "finance ledger notebook"],
        "insurance": ["medical hospital bill", "insurance policy review"],
        "hospital": ["hospital corridor emergency", "medical bill paperwork"],
        "bank": ["bank teller counter", "commercial bank building exterior"],
        "card": ["credit card tap pos", "wallet taking out credit card"],
        "nifty": ["stock trading floor screens", "candlestick chart plunging"],
        "market": ["financial market ticker display", "stock broker terminal"],
        "stock": ["stock chart fluctuations", "bull bear market graph"],
        "loss": ["distressed investor smartphone", "red trading loss portfolio"],
        "profit": ["smiling businessman smartphone", "green market chart rise"],
        "crash": ["stock market plunge red", "distressed trader head in hands"],
        "scam": ["shredding document machine", "cyber security padlock computer"],
        "money": ["counting rupee cash notes", "stacks of cash on table"],
    }

    matched_queries = []
    for trigger, query_list in finance_triggers.items():
        if trigger in clean_narration:
            matched_queries.extend(query_list)

    # Shuffle matched contextual queries to provide variety
    random.shuffle(matched_queries)
    for q in matched_queries:
        if q not in queries:
            queries.append(q)

    # 3. Add story-seed visual evidence anchor if available
    if story_seed and isinstance(story_seed, dict):
        anchor = story_seed.get("visual_evidence", "")
        if anchor and len(anchor.split()) <= 4 and anchor not in queries:
            queries.append(anchor)

    # 4. Fallback evergreen financial aesthetics if nothing matched
    fallbacks = [
        "candlestick stock chart",
        "financial analyst terminal",
        "counting money banknotes",
        "busy shopping street crowd",
        "smartphone banking application",
        "credit card payment terminal",
    ]
    random.shuffle(fallbacks)
    for fb in fallbacks:
        if fb not in queries:
            queries.append(fb)

    return queries[:4]


# ──────────────────────────────────────────────────────────────────────────────
#  Freshness-Enforced Pexels Downloader
# ──────────────────────────────────────────────────────────────────────────────

def fetch_fresh_pexels_broll(
    queries: list[str],
    pexels_key: str,
    output_path: Path,
    session_used_ids: Optional[set[int]] = None,
) -> Optional[dict]:
    """
    Searches Pexels Video API using dynamic queries with:
    1. Multi-result sampling (per_page=20).
    2. Orientation: portrait (h > w).
    3. Freshness check: skips IDs in session_used_ids AND persistent used_broll.json.
    4. Random pool sampling from top fresh matches (never just the 1st match!).
    """
    if not pexels_key or not queries:
        return None

    if session_used_ids is None:
        session_used_ids = set()

    persistent_used = load_used_broll()
    headers = {"Authorization": pexels_key}

    for query in queries:
        try:
            clean_q = urllib.parse.quote(query.strip())
            url = f"https://api.pexels.com/videos/search?query={clean_q}&per_page=20&orientation=portrait"
            res = requests.get(url, headers=headers, timeout=12)
            if res.status_code != 200:
                log.warning("Pexels query '%s' returned HTTP %d", query, res.status_code)
                continue

            videos = res.json().get("videos", [])
            fresh_candidates: list[tuple[int, str]] = []

            for v in videos:
                vid_id = v.get("id")
                if not vid_id:
                    continue
                # Skip if used in current video session or in past 30 days
                if vid_id in session_used_ids or str(vid_id) in persistent_used:
                    continue

                for vf in v.get("video_files", []):
                    w = vf.get("width", 0)
                    h = vf.get("height", 0)
                    link = vf.get("link")
                    # Strict vertical orientation (height > width)
                    if h > w and link:
                        fresh_candidates.append((vid_id, link))
                        break

            if not fresh_candidates:
                log.info("No fresh unused clips for query '%s' (all seen/used). Trying next query...", query)
                continue

            # Try the top fresh candidates (shuffled to prevent predictability)
            # until one passes the visual sanity check.
            pool = fresh_candidates[: min(5, len(fresh_candidates))]
            random.shuffle(pool)

            for selected_id, selected_link in pool:
                # Download video stream
                r = requests.get(selected_link, stream=True, timeout=25)
                if r.status_code != 200:
                    continue
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

                if not (output_path.exists() and output_path.stat().st_size > 50000):
                    continue

                # Black/empty clip guard: the release quality gate rejects
                # mostly-black assets; catch them at download time and try the
                # next candidate instead of killing the whole run at render.
                from src.agents.quality_gate import _mean_luma
                try:
                    luma = _mean_luma(output_path)
                except Exception as luma_err:
                    log.warning("Could not inspect clip %s for '%s' (%s); trying another candidate.", selected_id, query, luma_err)
                    session_used_ids.add(selected_id)
                    record_used_video(selected_id)
                    continue
                if luma < 12:
                    log.warning(
                        "Pexels clip %s for '%s' is mostly black (luma %.1f); trying another candidate.",
                        selected_id, query, luma,
                    )
                    session_used_ids.add(selected_id)
                    record_used_video(selected_id)
                    continue

                size_kb = output_path.stat().st_size // 1024
                log.info(
                    " ✓ Fresh Pexels video downloaded for '%s' (ID %s, %d KB, pool size: %d, luma %.0f)",
                    query,
                    selected_id,
                    size_kb,
                    len(fresh_candidates),
                    luma,
                )
                session_used_ids.add(selected_id)
                record_used_video(selected_id)
                return {
                    "video_id": selected_id,
                    "query": query,
                    "file_path": output_path,
                    "size_kb": size_kb,
                }

        except Exception as exc:
            log.warning("Pexels fetch attempt failed for '%s': %s", query, exc)

    return None
