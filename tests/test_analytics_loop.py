"""Regression tests for the analytics feedback loop.

The loop used to record empty metric rows and let the tuner "learn" from them.
These tests pin the fixed behaviour: empty fetches are retried, never recorded
as zeros, and the tuner ignores rows without real metrics.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.analytics.analytics_sensor import AnalyticsSensor, MAX_AUDIT_ATTEMPTS
from src.analytics.tuner_agent import PerformanceTuningAgent


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _old_post(title="Test Post", age_hours=60):
    return {
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
        "title": title,
        "topic": "some topic",
        "hook": "a hook?",
        "duration_seconds": 24.0,
        "ids": {"youtube": "abc123", "instagram": "999"},
        "audited": False,
    }


def _make_sensor(tmp_path: Path):
    return AnalyticsSensor(
        ledger_path=tmp_path / "publish_ledger.json",
        analytics_path=tmp_path / "analytics_ledger.json",
        deprecated_path=tmp_path / "deprecated_patterns.json",
    )


def test_empty_fetch_is_retried_not_recorded(tmp_path, monkeypatch):
    ledger = [_old_post()]
    _write_json(tmp_path / "publish_ledger.json", ledger)
    sensor = _make_sensor(tmp_path)
    monkeypatch.setattr(sensor, "_fetch_instagram_metrics", lambda _id: {})
    monkeypatch.setattr(sensor, "_fetch_youtube_metrics", lambda _id: {})

    result = sensor.audit_recent_posts()

    assert result["audited"] == 0
    assert result["retrying"] == 1
    saved = json.loads((tmp_path / "publish_ledger.json").read_text())
    assert saved[0]["audited"] is False
    assert saved[0]["audit_attempts"] == 1
    # No analytics record was written for a zero-data fetch
    analytics_path = tmp_path / "analytics_ledger.json"
    assert not analytics_path.exists() or json.loads(analytics_path.read_text()) == []


def test_empty_fetch_gives_up_after_max_attempts(tmp_path, monkeypatch):
    post = _old_post()
    post["audit_attempts"] = MAX_AUDIT_ATTEMPTS - 1
    _write_json(tmp_path / "publish_ledger.json", [post])
    sensor = _make_sensor(tmp_path)
    monkeypatch.setattr(sensor, "_fetch_instagram_metrics", lambda _id: {})
    monkeypatch.setattr(sensor, "_fetch_youtube_metrics", lambda _id: {})

    result = sensor.audit_recent_posts()

    assert result["fetch_failed"] == 1
    saved = json.loads((tmp_path / "publish_ledger.json").read_text())
    assert saved[0]["audited"] is True
    records = json.loads((tmp_path / "analytics_ledger.json").read_text())
    assert records[0]["status"] == "fetch_failed"


def test_real_metrics_are_recorded_and_marked_audited(tmp_path, monkeypatch):
    _write_json(tmp_path / "publish_ledger.json", [_old_post()])
    sensor = _make_sensor(tmp_path)
    monkeypatch.setattr(sensor, "_fetch_instagram_metrics", lambda _id: {})
    monkeypatch.setattr(sensor, "_fetch_youtube_metrics", lambda _id: {"views": 120, "likes": 4, "comments": 1})

    result = sensor.audit_recent_posts()

    assert result["audited"] == 1
    records = json.loads((tmp_path / "analytics_ledger.json").read_text())
    assert records[0]["status"] == "ok"
    assert records[0]["youtube"]["views"] == 120


def test_fresh_post_is_not_audited_yet(tmp_path, monkeypatch):
    _write_json(tmp_path / "publish_ledger.json", [_old_post(age_hours=5)])
    sensor = _make_sensor(tmp_path)
    monkeypatch.setattr(sensor, "_fetch_instagram_metrics", lambda _id: {})
    monkeypatch.setattr(sensor, "_fetch_youtube_metrics", lambda _id: {})

    result = sensor.audit_recent_posts()
    assert result["audited"] == 0 and result["retrying"] == 0


def _make_tuner(tmp_path: Path):
    return PerformanceTuningAgent(
        analytics_path=tmp_path / "analytics_ledger.json",
        publish_path=tmp_path / "publish_ledger.json",
        playbook_path=tmp_path / "tuning_playbook.json",
    )


def test_tuner_ignores_empty_rows_and_uses_baseline(tmp_path):
    rows = [
        {"title": f"post {i}", "instagram": {}, "youtube": {}, "duration_seconds": 24.0}
        for i in range(45)
    ]
    _write_json(tmp_path / "analytics_ledger.json", rows)
    _write_json(tmp_path / "publish_ledger.json", [])
    tuner = _make_tuner(tmp_path)

    playbook = tuner.generate_playbook()

    assert playbook["analyzed_videos_count"] == 0
    assert playbook["data_status"] == "waiting_for_real_analytics"
    assert playbook["optimal_runtime_seconds"] == 24.0  # baseline, not "learned"


def test_tuner_learns_only_from_real_rows(tmp_path, monkeypatch):
    rows = [
        {"title": "empty", "instagram": {}, "youtube": {}, "duration_seconds": 24.0},
        {"title": "fetch failed", "status": "fetch_failed", "instagram": {}, "youtube": {}, "duration_seconds": 24.0},
        {"title": "real 1", "status": "ok", "instagram": {}, "youtube": {"views": 500, "likes": 20}, "duration_seconds": 26.0, "hook": "h1", "topic": "t1"},
        {"title": "real 2", "status": "ok", "instagram": {"reach": 800, "saved": 30, "shares": 10}, "youtube": {}, "duration_seconds": 26.0, "hook": "h2", "topic": "t2"},
    ]
    _write_json(tmp_path / "analytics_ledger.json", rows)
    _write_json(tmp_path / "publish_ledger.json", [])
    tuner = _make_tuner(tmp_path)
    monkeypatch.setattr(tuner, "_synthesize_with_gemini", lambda *_args: {})

    playbook = tuner.generate_playbook()

    assert playbook["analyzed_videos_count"] == 2
    assert playbook["data_status"] == "learning_from_real_analytics"
