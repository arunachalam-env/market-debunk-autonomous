"""Tests for the autonomous topic bank (src/agents/topic_queue.py)."""
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import topic_queue


def _cand(cid="src-1", status="queued", thesis="Why your bank savings lose money",
          title="Bank savings trap", theme="MARKET_INVESTING", score=0.9,
          created_at=None, selected_at=None):
    return {
        "id": topic_queue._candidate_id(cid),
        "status": status,
        "score": score,
        "language": "en",
        "theme": theme,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "channel": "Money Pechu",
        "video_id": cid,
        "source_id": f"youtube:{cid}",
        "video_title": title,
        "thesis": thesis,
        "story_seed": {"concept_name": "Savings erosion"},
        "transcript_length": 1200,
        "research": {"source_url": f"https://www.youtube.com/watch?v={cid}",
                     "excerpt": "x" * 200, "researched_at": datetime.now(timezone.utc).isoformat()},
        **({"selected_at": selected_at} if selected_at else {}),
    }


@pytest.fixture
def qpath(tmp_path):
    return tmp_path / "topic_queue.json"


@pytest.fixture(autouse=True)
def fake_evaluator(monkeypatch):
    """Stub the heavy evaluator module for every test."""
    mod = types.SimpleNamespace(
        is_source_id_used=lambda sid: False,
        is_duplicate=lambda *a, **k: (False, 0.0, "ok"),
        get_current_target_domain=lambda: "MARKET_INVESTING",
    )
    monkeypatch.setitem(sys.modules, "src.agents.evaluator", mod)
    # ensure the lazy "from src.agents import evaluator" picks up the stub
    import src.agents as agents_pkg
    monkeypatch.setattr(agents_pkg, "evaluator", mod, raising=False)
    return mod


# 1. EMPTY QUEUE -> pipeline falls back to live discovery
def test_empty_queue_returns_none(qpath):
    assert topic_queue.select_candidate("MARKET_INVESTING", qpath) is None


def test_missing_queue_file_returns_none(tmp_path):
    assert topic_queue.select_candidate(None, tmp_path / "nope.json") is None


# 2. SELECTION: best candidate picked, marked selected, shape matches discover_topic
def test_select_marks_selected_and_returns_topic_shape(qpath):
    topic_queue.save_queue([_cand()], qpath)
    out = topic_queue.select_candidate("MARKET_INVESTING", qpath)
    assert out is not None
    for key in ("channel", "video_id", "source_id", "video_title", "thesis", "story_seed", "transcript_length", "queue_candidate_id"):
        assert key in out
    stored = topic_queue.load_queue(qpath)[0]
    assert stored["status"] == "selected"
    assert "selected_at" in stored


def test_domain_mismatch_deprioritised(qpath):
    topic_queue.save_queue([
        _cand("src-a", theme="CONSUMER_DEFENSE", score=0.99, thesis="Consumer topic"),
        _cand("src-b", theme="MARKET_INVESTING", score=0.5, thesis="Market topic"),
    ], qpath)
    out = topic_queue.select_candidate("MARKET_INVESTING", qpath)
    assert out["thesis"] == "Market topic"


# 3. STALE / DUPLICATE candidates are dropped, never selected
def test_stale_candidate_dropped(qpath):
    old = (datetime.now(timezone.utc) - timedelta(days=topic_queue.STALE_AFTER_DAYS + 1)).isoformat()
    topic_queue.save_queue([_cand(created_at=old)], qpath)
    assert topic_queue.select_candidate("MARKET_INVESTING", qpath) is None
    assert topic_queue.load_queue(qpath)[0]["status"] == "stale"


def test_duplicate_source_id_dropped(qpath, fake_evaluator):
    topic_queue.save_queue([_cand()], qpath)
    fake_evaluator.is_source_id_used = lambda sid: True
    assert topic_queue.select_candidate("MARKET_INVESTING", qpath) is None
    assert topic_queue.load_queue(qpath)[0]["status"] == "invalid"


def test_duplicate_thesis_dropped(qpath, fake_evaluator):
    topic_queue.save_queue([_cand()], qpath)
    def dup(*a, **k):
        return (True, 0.95, "too similar") if not k.get("enforce_domain_cooldown") is False or True else (False, 0.0, "")
    fake_evaluator.is_duplicate = lambda *a, **k: (True, 0.95, "dup")
    assert topic_queue.select_candidate("MARKET_INVESTING", qpath) is None
    assert topic_queue.load_queue(qpath)[0]["status"] == "invalid"


# 4. CONSUMPTION: only after successful publish; consumed never re-selected
def test_mark_consumed_only_on_publish_success(qpath):
    topic_queue.save_queue([_cand()], qpath)
    out = topic_queue.select_candidate("MARKET_INVESTING", qpath)
    assert topic_queue.mark_consumed(out["queue_candidate_id"], qpath) is True
    assert topic_queue.load_queue(qpath)[0]["status"] == "consumed"
    # consumed candidate must never come back
    assert topic_queue.select_candidate("MARKET_INVESTING", qpath) is None


def test_mark_consumed_unknown_id_is_noop(qpath):
    topic_queue.save_queue([_cand()], qpath)
    assert topic_queue.mark_consumed("tq-doesnotexist", qpath) is False
    assert topic_queue.load_queue(qpath)[0]["status"] == "queued"


# 5. FAILED PUBLISH: selected-but-unpublished returns to queued after reset window
def test_failed_publish_candidate_returns_to_queue(qpath):
    sel = (datetime.now(timezone.utc) - timedelta(hours=topic_queue.SELECTED_RESET_HOURS + 1)).isoformat()
    topic_queue.save_queue([_cand(status="selected", selected_at=sel)], qpath)
    out = topic_queue.select_candidate("MARKET_INVESTING", qpath)
    assert out is not None and out["thesis"] == "Why your bank savings lose money"


def test_recently_selected_stays_locked(qpath):
    sel = datetime.now(timezone.utc).isoformat()
    topic_queue.save_queue([_cand(status="selected", selected_at=sel)], qpath)
    assert topic_queue.select_candidate("MARKET_INVESTING", qpath) is None


# 6. BUILDER IDEMPOTENCY: re-running never duplicates a source or near-identical thesis
def test_build_is_idempotent(qpath, monkeypatch):
    import src.agents as agents_pkg
    fake_scan = lambda limit_per_channel=5: [
        {"channel": "Money Pechu", "video_id": "vid-1", "title": "Bank savings trap",
         "description": "d" * 60, "published_at": datetime.now(timezone.utc).isoformat()}
    ]
    fake_seed = lambda raw, title: {"thesis": "Why your bank savings lose money", "story_seed": {"concept_name": "x"}}
    fake_ta = types.SimpleNamespace(
        scan_all_channels_parallel=fake_scan,
        download_transcript=lambda vid: "transcript " * 50,
        summarize_to_story_seed=fake_seed,
    )
    monkeypatch.setattr(agents_pkg, "topic_agent", fake_ta, raising=False)

    s1 = topic_queue.build_queue(qpath)
    s2 = topic_queue.build_queue(qpath)
    assert s1["added"] == 1
    assert s2["added"] == 0, "second build must not duplicate the same source"
    queue = topic_queue.load_queue(qpath)
    assert len(queue) == 1
    assert queue[0]["language"] == "en"
    assert queue[0]["research"]["source_url"].endswith("watch?v=vid-1")


def test_build_respects_max_candidates(qpath, monkeypatch):
    import src.agents as agents_pkg
    fake_scan = lambda limit_per_channel=5: [
        {"channel": "Ch", "video_id": f"vid-{i}", "title": f"Topic {i}",
         "description": "d" * 60, "published_at": datetime.now(timezone.utc).isoformat()}
        for i in range(10)
    ]
    unique = ["bank savings quietly lose value",
              "credit cards hide their real cost",
              "gold schemes mislead loyal buyers",
              "insurance agents oversell weak policies",
              "mutual fund fees eat compounding",
              "loan EMIs stretch household budgets",
              "ipo hype burns small investors",
              "deposit laddering beats idle cash",
              "trading apps nudge reckless bets",
              "salary accounts carry hidden charges"]
    it = iter(unique)
    fake_seed = lambda raw, title: {"thesis": next(it), "story_seed": {}}
    fake_ta = types.SimpleNamespace(
        scan_all_channels_parallel=fake_scan,
        download_transcript=lambda vid: "t " * 40,
        summarize_to_story_seed=fake_seed,
    )
    monkeypatch.setattr(agents_pkg, "topic_agent", fake_ta, raising=False)
    topic_queue.build_queue(qpath)
    queue = topic_queue.load_queue(qpath)
    assert len(queue) == topic_queue.MAX_CANDIDATES
