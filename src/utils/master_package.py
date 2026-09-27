"""Create a reusable English master package for the Tamil companion channel."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional


def export_master_package(
    run_dir: Path,
    thesis: str,
    script: dict,
    visuals: list[dict],
    source_id: str = "",
    strategic_brief: Optional[dict] = None,
    source_excerpt: str = "",
) -> Path:
    """Copy scene assets plus the scene-aligned English script into one package."""
    package_dir = run_dir / "master_visual_package"
    assets_dir = package_dir / "visuals"
    assets_dir.mkdir(parents=True, exist_ok=True)

    manifest_scenes = []
    for visual in sorted(visuals, key=lambda item: item["scene_id"]):
        source = Path(visual["asset_path"])
        if not source.is_file():
            raise RuntimeError(f"Cannot export missing master visual: {source}")
        filename = f"scene_{visual['scene_id']}{source.suffix.lower() or '.jpg'}"
        shutil.copy2(source, assets_dir / filename)
        manifest_scenes.append({
            "scene_id": visual["scene_id"],
            "asset": f"visuals/{filename}",
            "type": visual.get("asset_type", "video" if source.suffix.lower() in (".mp4", ".mov", ".webm") else "image")
        })

    manifest = {
        "schema_version": 1,
        "thesis": thesis,
        "source_id": source_id,
        "script": script,
        "scenes": manifest_scenes,
        "strategic_brief": strategic_brief,
        # Original-reporting excerpt so the Tamil gate can catch misreadings
        # the same way the English gate does (see fact_check_agent).
        "source_excerpt": source_excerpt,
    }
    (package_dir / "master_package.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return package_dir
