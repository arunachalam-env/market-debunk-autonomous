"""Story Mode illustration agent.

Generates one storybook illustration per scene for the "Arun Stories" format,
replacing generic stock B-roll with a consistent hand-painted world.

Provider policy (per owner decision, 2026-09-27):
  Gemini image models with the locked character sheet as a reference image are
  the ONLY acceptable illustration source (best character consistency; key
  rotation across the existing Gemini keys). Pollinations/stock substitutions
  were rejected on quality grounds and must never reach a published video.
  On quota exhaustion or provider failure this agent raises
  StoryImageUnavailable, failing the run closed so it retries on the next
  scheduled trigger after quota reset instead of shipping off-model frames.
"""
from __future__ import annotations

import base64
import os
import shutil
import time
from pathlib import Path
from typing import Optional

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


_HOOK_FRAME = (
    " This is the OPENING frame of the video: make it visually arresting - an unusual, "
    "slightly paradoxical composition that makes a scroller stop and wonder what they are "
    "looking at, while staying fully inside the locked style, palette, and world."
)


def _build_prompt(scene: dict, has_sheet: bool) -> str:
    beat = " ".join(str(scene.get("visual_prompt", "")).split())
    if scene.get("scene_id") == 1:
        beat = beat + _HOOK_FRAME
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


class StoryImageUnavailable(RuntimeError):
    """Raised when no acceptable (Gemini + character sheet) image can be produced."""


_RETRY_ROUNDS = 3
_RETRY_SLEEP_SECONDS = 20


def generate_scene_image(scene: dict, output_dir: Path) -> Optional[Path]:
    """Generate one storybook illustration for a scene.

    Fail-closed: raises StoryImageUnavailable when Gemini cannot deliver, so the
    run halts instead of falling back to off-model substitutes.
    """
    scene_id = scene.get("scene_id", 0)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"scene_{scene_id}.png"

    # Externally supplied images mode (agent-rendered scene packs): when
    # STORY_IMAGES_INBOX is set, scenes come ONLY from that directory and any
    # missing scene halts the run - never an API call, never a substitute.
    inbox = (os.environ.get("STORY_IMAGES_INBOX") or "").strip()
    if inbox:
        supplied = Path(inbox) / f"scene_{scene_id}.png"
        if supplied.is_file() and supplied.stat().st_size > 10000:
            shutil.copy2(supplied, output_path)
            log.info("Scene %s story image supplied externally (%d bytes)", scene_id, output_path.stat().st_size)
            return output_path
        raise StoryImageUnavailable(
            f"Scene {scene_id}: externally supplied image missing or too small at {supplied}"
        )

    sheet_b64 = None
    if CHARACTER_SHEET_PATH.exists() and CHARACTER_SHEET_PATH.stat().st_size > 10000:
        sheet_b64 = base64.b64encode(CHARACTER_SHEET_PATH.read_bytes()).decode("ascii")
    else:
        log.warning("Character sheet missing at %s - using written character bible", CHARACTER_SHEET_PATH)

    prompt = _build_prompt(scene, has_sheet=bool(sheet_b64))

    for attempt in range(1, _RETRY_ROUNDS + 1):
        if _try_gemini_image(prompt, sheet_b64, output_path):
            log.info("Scene %s story image via Gemini (%d bytes)", scene_id, output_path.stat().st_size)
            return output_path
        if attempt < _RETRY_ROUNDS:
            log.info("Scene %s Gemini attempt %d/%d failed; retrying in %ds",
                     scene_id, attempt, _RETRY_ROUNDS, _RETRY_SLEEP_SECONDS)
            time.sleep(_RETRY_SLEEP_SECONDS)
    raise StoryImageUnavailable(
        f"Scene {scene_id}: Gemini image generation unavailable on all keys/models "
        f"after {_RETRY_ROUNDS} attempts. Halting run (fail-closed); the next scheduled "
        f"run retries after quota reset. Off-model substitutes are disabled by owner decision."
    )
