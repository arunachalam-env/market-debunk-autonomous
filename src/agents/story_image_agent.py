"""Story Mode illustration agent.

Generates one storybook illustration per scene for the "Arun Stories" format,
replacing generic stock B-roll with a consistent hand-painted world.

Provider chain (per scene, independent - one failure never kills the run):
  1. Gemini image models with the locked character sheet as a reference image
     (best character consistency; key rotation across the existing Gemini keys).
  2. Pollinations.ai (free, keyless FLUX) with the written character bible.
  3. Returns None -> visual_agent falls back to the classic Pexels B-roll path.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="story_images")

CHARACTER_SHEET_PATH = Path("assets") / "character" / "arun_sheet.png"

# Locked visual identity. Keep in sync with CHARACTER.md.
STYLE_TAG = (
    "Muted hand-painted storybook illustration, graphic-novel watercolor texture, "
    "warm sepia-and-olive palette with soft amber lamplight, slightly desaturated colors, "
    "clean lines, gentle painterly shading, calm cinematic composition, cozy but melancholic mood."
)

CHARACTER_BIBLE = (
    "The recurring character is Arun: a young Indian man, 24, wheatish skin, "
    "dark slightly-messy wavy hair, warm brown eyes, light stubble, wearing a casual "
    "olive-green hoodie. He lives in a modest Chennai apartment with a small wooden desk, "
    "a laptop, a steel filter-coffee tumbler, and a window showing dusk city rooftops."
)

NEGATIVE = (
    "No readable text, no words, no letters, no captions, no title, no logo, no watermark, "
    "no black bars, no letterbox, no photorealism, no 3D render look, no Pixar style."
)

_IMAGE_MODELS = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-2.0-flash-image",
]


def _gemini_keys() -> list[str]:
    keys = [
        getattr(settings, "GEMINI_IMAGE_API_KEY", "") or "",
        getattr(settings, "GEMINI_API_KEY", "") or "",
        getattr(settings, "GEMINI_SCRIPT_API_KEY", "") or "",
    ]
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _build_prompt(scene: dict, has_sheet: bool) -> str:
    beat = " ".join(str(scene.get("visual_prompt", "")).split())
    if has_sheet:
        return (
            "Using the attached reference image, keep the EXACT same character "
            "(same face, same olive hoodie), the same apartment world, and the same "
            "muted storybook illustration style and palette. "
            f"Draw this new scene with him: {beat}. {NEGATIVE}"
        )
    return f"{STYLE_TAG} {CHARACTER_BIBLE} Scene: {beat}. {NEGATIVE}"


def _try_gemini_image(prompt: str, sheet_b64: Optional[str], output_path: Path) -> bool:
    keys = _gemini_keys()
    if not keys:
        return False
    parts = []
    if sheet_b64:
        parts.append({"inline_data": {"mime_type": "image/png", "data": sheet_b64}})
    parts.append({"text": prompt})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    for model in _IMAGE_MODELS:
        for key in keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                res = requests.post(url, json=payload, timeout=120)
                if res.status_code != 200:
                    log.info("Gemini image %s returned HTTP %s", model, res.status_code)
                    continue
                data = res.json()
                for cand in data.get("candidates", []):
                    for part in (cand.get("content") or {}).get("parts", []):
                        inline = part.get("inlineData") or part.get("inline_data") or {}
                        b64 = inline.get("data")
                        if b64:
                            output_path.write_bytes(base64.b64decode(b64))
                            if output_path.stat().st_size > 10000:
                                return True
                log.info("Gemini image %s returned no image part", model)
            except Exception as exc:  # noqa: BLE001 - never let one provider kill the run
                log.info("Gemini image %s failed: %s", model, exc)
            time.sleep(1.0)
    return False


def _try_pollinations(prompt: str, output_path: Path) -> bool:
    try:
        url = (
            "https://image.pollinations.ai/prompt/" + quote(prompt[:1500])
            + "?width=1080&height=1680&seed=24&nologo=true&model=flux"
        )
        res = requests.get(url, timeout=180)
        ctype = (res.headers.get("Content-Type") or "").lower()
        if res.status_code == 200 and "image" in ctype and len(res.content) > 10000:
            output_path.write_bytes(res.content)
            return True
        log.info("Pollinations returned HTTP %s (%s)", res.status_code, ctype)
    except Exception as exc:  # noqa: BLE001
        log.info("Pollinations image failed: %s", exc)
    return False


def generate_scene_image(scene: dict, output_dir: Path) -> Optional[Path]:
    """Generate one storybook illustration for a scene. Returns None on total failure."""
    scene_id = scene.get("scene_id", 0)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"scene_{scene_id}.png"

    sheet_b64 = None
    if CHARACTER_SHEET_PATH.exists() and CHARACTER_SHEET_PATH.stat().st_size > 10000:
        sheet_b64 = base64.b64encode(CHARACTER_SHEET_PATH.read_bytes()).decode("ascii")
    else:
        log.warning("Character sheet missing at %s - using written character bible", CHARACTER_SHEET_PATH)

    prompt = _build_prompt(scene, has_sheet=bool(sheet_b64))

    if _try_gemini_image(prompt, sheet_b64, output_path):
        log.info("Scene %s story image via Gemini (%d bytes)", scene_id, output_path.stat().st_size)
        return output_path
    if _try_pollinations(prompt, output_path):
        log.info("Scene %s story image via Pollinations (%d bytes)", scene_id, output_path.stat().st_size)
        return output_path
    log.warning("Scene %s story image failed on all providers; caller will fall back to B-roll", scene_id)
    return None
