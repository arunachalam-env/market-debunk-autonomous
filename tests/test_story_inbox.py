"""Tests for externally supplied story images mode (STORY_IMAGES_INBOX)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import story_image_agent


def _fake_png(path: Path, size: int = 20000) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * size)
    return path


def test_inbox_image_is_used_without_api(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    out = tmp_path / "out"
    inbox.mkdir()
    _fake_png(inbox / "scene_3.png")
    monkeypatch.setenv("STORY_IMAGES_INBOX", str(inbox))
    # any API attempt must explode the test
    monkeypatch.setattr(story_image_agent, "_try_gemini_image", lambda *a, **k: (_ for _ in ()).throw(AssertionError("API called")))
    res = story_image_agent.generate_scene_image({"scene_id": 3, "visual_prompt": "x"}, out)
    assert res == out / "scene_3.png"
    assert res.stat().st_size > 10000


def test_inbox_missing_scene_fails_closed(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("STORY_IMAGES_INBOX", str(inbox))
    monkeypatch.setattr(story_image_agent, "_try_gemini_image", lambda *a, **k: (_ for _ in ()).throw(AssertionError("API called")))
    with pytest.raises(story_image_agent.StoryImageUnavailable):
        story_image_agent.generate_scene_image({"scene_id": 7, "visual_prompt": "x"}, tmp_path / "out")


def test_inbox_tiny_file_fails_closed(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _fake_png(inbox / "scene_1.png", size=500)
    monkeypatch.setenv("STORY_IMAGES_INBOX", str(inbox))
    with pytest.raises(story_image_agent.StoryImageUnavailable):
        story_image_agent.generate_scene_image({"scene_id": 1, "visual_prompt": "x"}, tmp_path / "out")
