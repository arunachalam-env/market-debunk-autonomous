"""
src/agents/topic_queue.py

Autonomous Topic Bank - a pre-researched queue of source-backed video topics.

Why this exists: the daily pipeline used to discover its topic at run time,
which made every run depend on live scanning being healthy at that exact
moment. A separate evening workflow (topic_queue.yml) now pre-builds 3-5
vetted candidates into data/topic_queue.json, so the 09:17 IST run simply
consumes the best due candidate and only falls back to live discovery when
the bank is empty or every candidate fails validation.

Lifecycle:
  queued   -> vetted by the evening builder (anti-repeat, domain, source ok)
  selected -> picked by a pipeline run; returns to "queued" if not consumed
              within SELECTED_RESET_HOURS (failed runs retry the same topic)
  consumed -> marked only after a successful publish (never re-selected)
  stale    -> too old to be news-safe; dropped by the builder

Candidate schema (one JSON object per entry):
  id, status, score, language, theme, created_at,
  channel, video_id, source_id, video_title, thesis, story_seed,
  transcript_length, research: {source_url, excerpt, researched_at}
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

log = get_logger(__name__, phase="topic_queue")

QUEUE_PATH = Path("data") / "topic_queue.json"

MAX_CANDIDATES = 5
STALE_AFTER_DAYS = 5
SELECTED_RESET_HOURS = 36
_INTRA_QUEUE_DUP_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
#  Persistence
# ---------------------------------------------------------------------------

def load_queue(path: Optional[Path] = None) -> list[dict]:
    p = path or QUEUE_PATH
    if not p.is_file():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict) and c.get("id")]
    except Exception as exc:
        log.error("Failed to parse %s: %s - treating queue as empty", p, exc)
    return []


def save_queue(candidates: list[dict], path: Optional[Path] = None) -> None:
    p = path or QUEUE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(candidates, f, indent=2, ensure_ascii=False)


def _candidate_id(source_id: str) -> str:
    return "tq-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]


def _tokens(text: str) -> set:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in (text or "")).split() if len(t) > 2}


def _too_similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= _INTRA_QUEUE_DUP_THRESHOLD


# ---------------------------------------------------------------------------
#  Selection (called by the pipeline run)
# ---------------------------------------------------------------------------

def select_candidate(target_domain: Optional[str] = None, path: Optional[Path] = None) -> Optional[dict]:
    """
    Pick the best due queued candidate for this run.

    Re-validates anti-repeat and domain rules at selection time (the bank was
    built hours earlier; the world may have moved). Invalid candidates are
    dropped from the queue. Returns a topic_data dict in exactly the shape
    topic_agent.discover_topic() produces, plus queue_candidate_id.
    """
    from src.agents import evaluator  # lazy: heavy module

    queue = load_queue(path)
    if not queue:
        return None

    reset_stale_selected(queue)
    now = datetime.now(timezone.utc)
    changed = False
    usable = []

    for cand in queue:
        if cand.get("status") != "queued":
            continue
        created = _parse_ts(cand.get("created_at"))
        if created and (now - created) > timedelta(days=STALE_AFTER_DAYS):
            cand["status"] = "stale"
            changed = True
            log.info("Dropping stale candidate '%s' (>%dd old)", cand.get("thesis", "")[:50], STALE_AFTER_DAYS)
            continue
        usable.append(cand)

    def _domain_ok(c: dict) -> bool:
        return not target_domain or c.get("theme") in (None, "", target_domain)

    # Prefer candidates matching the current slot domain, then by score.
    usable.sort(key=lambda c: (not _domain_ok(c), -float(c.get("score", 0.0))))

    for cand in usable:
        thesis = cand.get("thesis", "")
        title = cand.get("video_title", "")
        source_id = cand.get("source_id", "")

        if source_id and evaluator.is_source_id_used(source_id):
            cand["status"] = "invalid"
            changed = True
            log.info("Queue candidate dropped: source_id already published (%s)", source_id)
            continue
        is_title_dup, _, reason = evaluator.is_duplicate(title, threshold=0.88, enforce_domain_cooldown=False)
        if is_title_dup:
            cand["status"] = "invalid"
            changed = True
            log.info("Queue candidate dropped: title blocked (%s)", reason)
            continue
        is_dup, _, reason = evaluator.is_duplicate(
            thesis, enforce_domain_cooldown=True, enforce_slot_domain=True
        )
        if is_dup:
            cand["status"] = "invalid"
            changed = True
            log.info("Queue candidate dropped: thesis blocked (%s)", reason)
            continue

        cand["status"] = "selected"
        cand["selected_at"] = now.isoformat()
        save_queue(queue, path)
        log.info("✓ Topic bank: consuming candidate '%s' (score %.2f)", thesis[:60], float(cand.get("score", 0.0)))
        return {
            "channel": cand.get("channel", "Topic Bank"),
            "video_id": cand.get("video_id", ""),
            "source_id": source_id,
            "video_title": title,
            "thesis": thesis,
            "story_seed": cand.get("story_seed", {}),
            "transcript_length": int(cand.get("transcript_length", 0)),
            "queue_candidate_id": cand["id"],
        }

    if changed:
        save_queue(queue, path)
    return None


def mark_consumed(candidate_id: Optional[str], path: Optional[Path] = None) -> bool:
    """Mark a selected candidate consumed. Call ONLY after a successful publish."""
    if not candidate_id:
        return False
    queue = load_queue(path)
    for cand in queue:
        if cand.get("id") == candidate_id:
            cand["status"] = "consumed"
            cand["consumed_at"] = datetime.now(timezone.utc).isoformat()
            save_queue(queue, path)
            log.info("Topic bank: candidate %s marked consumed", candidate_id)
            return True
    return False


def reset_stale_selected(queue: list[dict]) -> int:
    """
    Failed runs leave candidates in 'selected'. After SELECTED_RESET_HOURS a
    selected-but-never-published candidate goes back to 'queued' so the next
    run can retry the same topic instead of losing it.
    """
    now = datetime.now(timezone.utc)
    reset = 0
    for cand in queue:
        if cand.get("status") != "selected":
            continue
        sel = _parse_ts(cand.get("selected_at"))
        if sel and (now - sel) > timedelta(hours=SELECTED_RESET_HOURS):
            cand["status"] = "queued"
            cand.pop("selected_at", None)
            reset += 1
            log.info("Topic bank: returning unpublished candidate '%s' to queued", cand.get("thesis", "")[:50])
    return reset


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Builder (called by the evening topic_queue.yml workflow)
# ---------------------------------------------------------------------------

def build_queue(path: Optional[Path] = None, max_candidates: int = MAX_CANDIDATES) -> dict:
    """
    Top up the topic bank with fresh source-backed candidates, using the same
    battle-tested sourcing pipeline as run-time discovery (channel scan ->
    transcript -> story seed -> anti-repeat gates). Idempotent: re-running
    never duplicates an already-queued source or a near-identical thesis.
    """
    from src.agents import evaluator, topic_agent  # lazy: heavy modules

    queue = load_queue(path)
    reset_stale_selected(queue)

    now = datetime.now(timezone.utc)
    before = len(queue)
    # Drop stale entries and fully-consumed history older than 14 days.
    queue = [
        c for c in queue
        if not (
            (c.get("status") == "queued" and (_parse_ts(c.get("created_at")) or now) < now - timedelta(days=STALE_AFTER_DAYS))
            or (c.get("status") == "consumed" and (_parse_ts(c.get("consumed_at")) or now) < now - timedelta(days=14))
        )
    ]

    existing_ids = {c.get("source_id") for c in queue}
    existing_theses = [c.get("thesis", "") for c in queue if c.get("status") in ("queued", "selected")]
    open_slots = max_candidates - sum(1 for c in queue if c.get("status") in ("queued", "selected"))

    added = 0
    if open_slots > 0:
        target_domain = evaluator.get_current_target_domain()
        for cand in topic_agent.scan_all_channels_parallel(limit_per_channel=5):
            if added >= open_slots:
                break
            source_id = f"youtube:{cand['video_id']}"
            title = cand["title"]
            if source_id in existing_ids:
                continue
            is_title_dup, _, _ = evaluator.is_duplicate(title, threshold=0.88, enforce_domain_cooldown=False)
            if is_title_dup:
                continue

            transcript = topic_agent.download_transcript(cand["video_id"])
            if transcript and transcript.strip():
                raw_content, ctype = transcript, "transcript"
            else:
                desc = (cand.get("description") or "").strip()
                raw_content = (
                    f"VIDEO TITLE: {title}\nCHANNEL: {cand['channel']}\n\nVIDEO DESCRIPTION:\n{desc}"
                    if len(desc) >= 30 else f"VIDEO TITLE: {title}\nCHANNEL: {cand['channel']}"
                )
                ctype = "description" if len(desc) >= 30 else "title"

            seed_data = topic_agent.summarize_to_story_seed(raw_content, title)
            thesis = seed_data.get("thesis", title)
            is_dup, _, _ = evaluator.is_duplicate(thesis, enforce_domain_cooldown=True, enforce_slot_domain=False)
            if is_dup:
                continue
            if any(_too_similar(thesis, t) for t in existing_theses):
                log.info("Skipping queue-near-duplicate thesis '%s'", thesis[:50])
                continue

            published_at = _parse_ts(cand.get("published_at"))
            age_h = (now - published_at).total_seconds() / 3600 if published_at else 72.0
            score = round(1.0 / (1.0 + max(age_h, 0.0) / 24.0), 4)

            entry = {
                "id": _candidate_id(source_id),
                "status": "queued",
                "score": score,
                "language": "en",
                "theme": target_domain,
                "created_at": now.isoformat(),
                "channel": cand["channel"],
                "video_id": cand["video_id"],
                "source_id": source_id,
                "video_title": title,
                "thesis": thesis,
                "story_seed": seed_data.get("story_seed", {}),
                "transcript_length": len(raw_content),
                "research": {
                    "source_url": f"https://www.youtube.com/watch?v={cand['video_id']}",
                    "excerpt": raw_content[:1500],
                    "researched_at": now.isoformat(),
                    "content_type": ctype,
                },
            }
            queue.append(entry)
            existing_ids.add(source_id)
            existing_theses.append(thesis)
            added += 1
            log.info("✓ Queued candidate '%s' (score %.2f)", thesis[:60], score)

    save_queue(queue, path)
    summary = {"before": before, "after": len(queue), "added": added}
    log.info("Topic bank build complete: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Market Debunk autonomous topic bank")
    parser.add_argument("--build", action="store_true", help="Top up data/topic_queue.json")
    args = parser.parse_args()
    if args.build:
        summary = build_queue()
        print(json.dumps(summary))
    else:
        queue = load_queue()
        print(json.dumps({"candidates": len(queue), "queued": sum(1 for c in queue if c.get("status") == "queued")}))


if __name__ == "__main__":
    main()
