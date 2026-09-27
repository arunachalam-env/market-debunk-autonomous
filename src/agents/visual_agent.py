import os
import shutil
import time
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger
from src.utils.config import settings
from src.agents import broll_agent

log = get_logger(__name__, phase="visual_sourcing")

# Fixed presenter avatar asset path
PRESENTER_AVATAR_PATH = Path("assets") / "presenter_avatar.png"


def source_all_visuals(scenes: list, output_dir: Path, story_seed: Optional[dict] = None) -> list:
    """
    Sources visual assets for each scene:
    - Scene 1 (The Hook): User's real face/portrait (assets/presenter_avatar.png) to anchor authority.
    - Scenes 2 to (N-1): Contextual dynamic Pexels B-roll (documents, charts, money, screens).
    - Scene N (The Closer / CTA): User's real face/portrait (assets/presenter_avatar.png).
    - Fallback: If Pexels fails on intermediate scenes, fallback to presenter portrait or available loop.
    - Zero GCP / Zero Vertex AI: No external paid cloud image gen dependencies.
    """
    if isinstance(output_dir, str):
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    visual_paths = []
    pexels_key = settings.PEXELS_API_KEY
    used_video_ids: set[int] = set()

    total_scenes = len(scenes)

    for idx, scene in enumerate(scenes):
        scene_id = scene["scene_id"]
        is_first_scene = (idx == 0 or scene_id == 1)
        is_last_scene = (idx == total_scenes - 1 or scene_id == total_scenes)

        # ── 100% Dynamic High-Velocity B-Roll for All Scenes (Approach A) ──
        sourced = False
        # -- Story Mode: illustrated story frames first ------------------------
        if bool(getattr(settings, "STORY_MODE", False)):
            from src.agents import story_image_agent
            try:
                image_path = story_image_agent.generate_scene_image(scene, output_dir)
            except story_image_agent.StoryImageUnavailable:
                raise  # fail-closed: never substitute off-model visuals
            except Exception as story_err:  # noqa: BLE001 - unexpected errors also halt
                raise story_image_agent.StoryImageUnavailable(
                    f"Scene {scene_id}: unexpected story-image error: {story_err}"
                ) from story_err
            if image_path:
                visual_paths.append({
                    "scene_id": scene_id,
                    "asset_type": "image",
                    "asset_path": str(image_path.resolve()),
                    "source": "story_image",
                })
                log.info(" Scene %d story illustration sourced", scene_id)
                sourced = True
        if not sourced and pexels_key:
            video_filename = f"scene_{scene_id}.mp4"
            video_filepath = output_dir / video_filename
            queries = broll_agent.generate_scene_queries(scene, story_seed=story_seed)
            log.info("Scene %d dynamic B-roll query options: %s", scene_id, queries)

            broll_result = broll_agent.fetch_fresh_pexels_broll(
                queries=queries,
                pexels_key=pexels_key,
                output_path=video_filepath,
                session_used_ids=used_video_ids,
            )
            if broll_result:
                visual_paths.append({
                    "scene_id": scene_id,
                    "asset_type": "video",
                    "asset_path": str(video_filepath.resolve()),
                    "source": "pexels",
                    "video_id": broll_result.get("video_id"),
                    "query": broll_result.get("query"),
                })
                log.info(" ✓ Scene %d fresh B-roll sourced (Pexels ID %s)", scene_id, broll_result.get("video_id"))
                sourced = True

        if not sourced:
            # Fallback to high-quality dynamic financial B-roll loop from assets/broll/
            broll_fallback = Path("assets/broll/broll_1.mp4")
            if not broll_fallback.exists():
                broll_fallback = Path("assets/broll/broll_2.mp4")

            if broll_fallback.exists() and broll_fallback.stat().st_size > 0:
                visual_paths.append({
                    "scene_id": scene_id,
                    "asset_type": "video",
                    "asset_path": str(broll_fallback.resolve()),
                    "source": "fallback_broll"
                })
                log.info(" ✓ Scene %d sourced via fallback cinematic B-roll (%s)", scene_id, broll_fallback.name)
            else:
                raise RuntimeError(f"Could not source B-roll for scene {scene_id} and fallback video missing.")
    return visual_paths
