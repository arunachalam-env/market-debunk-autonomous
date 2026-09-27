"""Regression: a mostly-black Pexels clip must not be accepted at download time.

Run 13 died at the render gate ("scene 4 visual asset is mostly black/empty")
because the B-roll downloader accepted the first downloaded clip without a
visual sanity check. The downloader now checks mean luma right after download
and tries the next candidate instead.
"""
from pathlib import Path
from unittest.mock import patch

import src.agents.broll_agent as broll


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, body=b"x" * 60000):
        self.status_code = status_code
        self._json = json_data or {}
        self._body = body

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1024):
        yield self._body


def _search_payload(video_ids):
    return {
        "videos": [
            {
                "id": vid,
                "video_files": [{"width": 360, "height": 640, "link": f"https://files.test/{vid}.mp4"}],
            }
            for vid in video_ids
        ]
    }


def test_black_clip_is_skipped_for_next_candidate(tmp_path):
    search = _FakeResponse(json_data=_search_payload([101, 102]))
    downloads = {}

    def fake_get(url, **kwargs):
        if "api.pexels.com" in url:
            return search
        vid = int(url.rsplit("/", 1)[1].split(".")[0])
        downloads[vid] = downloads.get(vid, 0) + 1
        return _FakeResponse()

    recorded = []

    # First inspected clip reads as black (luma < 12), second is fine.
    with patch.object(broll.requests, "get", side_effect=fake_get), \
         patch.object(broll.random, "shuffle", lambda pool: None), \
         patch.object(broll, "load_used_broll", return_value={}), \
         patch.object(broll, "record_used_video", side_effect=recorded.append), \
         patch("src.agents.quality_gate._mean_luma", side_effect=[4.0, 40.0]):
        result = broll.fetch_fresh_pexels_broll(
            ["price comparison"], "test-key", tmp_path / "scene.mp4", session_used_ids=set()
        )

    assert result is not None
    assert result["video_id"] == 102
    # The black clip was downloaded, rejected, and blacklisted from future reuse.
    assert downloads.get(101) == 1
    assert 101 in recorded
    assert 102 in recorded


def test_all_black_clips_falls_through_to_next_query(tmp_path):
    searches = {
        "q1": _FakeResponse(json_data=_search_payload([201])),
        "q2": _FakeResponse(json_data=_search_payload([202])),
    }

    def fake_get(url, **kwargs):
        if "api.pexels.com" in url:
            return searches["q1"] if "q1" in url else searches["q2"]
        return _FakeResponse()

    with patch.object(broll.requests, "get", side_effect=fake_get), \
         patch.object(broll, "load_used_broll", return_value={}), \
         patch.object(broll, "record_used_video", lambda vid: None), \
         patch("src.agents.quality_gate._mean_luma", side_effect=[3.0, 55.0]):
        result = broll.fetch_fresh_pexels_broll(
            ["q1", "q2"], "test-key", tmp_path / "scene.mp4", session_used_ids=set()
        )

    assert result is not None
    assert result["video_id"] == 202
    assert result["query"] == "q2"
