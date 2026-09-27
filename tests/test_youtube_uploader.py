"""Regression test: uploads must carry the synthetic-media disclosure."""
import sys
import types
from pathlib import Path


def test_upload_sets_contains_synthetic_media(monkeypatch, tmp_path):
    captured = {}

    class FakeInsert:
        def __init__(self, body):
            captured["body"] = body

        def next_chunk(self):
            return None, {"id": "vid123"}

    class FakeVideos:
        def insert(self, part=None, body=None, media_body=None):
            return FakeInsert(body)

    class FakeService:
        def videos(self):
            return FakeVideos()

    fake_media = types.ModuleType("googleapiclient.http")

    class FakeMediaFileUpload:
        def __init__(self, *a, **k):
            pass

    fake_media.MediaFileUpload = FakeMediaFileUpload
    monkeypatch.setitem(sys.modules, "googleapiclient.http", fake_media)

    from src.publishing import youtube_uploader

    monkeypatch.setattr(youtube_uploader, "_get_authenticated_service", lambda: FakeService())
    monkeypatch.setattr(youtube_uploader.settings, "ENABLE_YT_UPLOAD", True)
    monkeypatch.setattr(youtube_uploader.settings, "ALLOW_PUBLICATION", True)
    monkeypatch.setattr(youtube_uploader.settings, "YT_CLIENT_ID", "x")
    monkeypatch.setattr(youtube_uploader.settings, "YT_CLIENT_SECRET", "x")
    monkeypatch.setattr(youtube_uploader.settings, "YT_REFRESH_TOKEN", "x")

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00")
    result = youtube_uploader.upload_video(vid, "T", "d", ["shorts"])
    assert result == "vid123"
    assert captured["body"]["status"]["containsSyntheticMedia"] is True
