"""
src/analytics/analytics_sensor.py

Closed-Loop Analytics Feedback Sensor for Market Debunk Pipeline.
Audits video performance ~48 hours post-upload:
1. Queries Meta Graph API for Instagram Reels metrics (plays, reach, saves, shares, watch time).
2. Queries YouTube Data API for Shorts statistics (views, likes, comments).
3. Evaluates retention benchmarks (<50% APV threshold).
4. Automatically flags underperforming hook structures and topics into `deprecated_patterns.json`.
5. Passes negative guidance to script generation models so they learn from real algorithmic feedback.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="analytics_sensor")

# Give up retrying a post's metrics fetch after this many pipeline runs.
# Each run is a new chance for transient API/secret failures to heal, but a
# permanently broken credential should not keep a post unaudited forever.
MAX_AUDIT_ATTEMPTS = 5


class AnalyticsSensor:
    """Collects 48-hour social metrics and flags underperforming patterns."""

    def __init__(
        self,
        ledger_path: Optional[Path] = None,
        analytics_path: Optional[Path] = None,
        deprecated_path: Optional[Path] = None,
    ):
        data_dir = settings.DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)

        self.ledger_path = Path(ledger_path) if ledger_path else data_dir / "publish_ledger.json"
        self.analytics_path = Path(analytics_path) if analytics_path else data_dir / "analytics_ledger.json"
        self.deprecated_path = Path(deprecated_path) if deprecated_path else data_dir / "deprecated_patterns.json"

    @staticmethod
    def _has_metrics(metrics: Optional[dict]) -> bool:
        """True only when a platform fetch returned at least one real metric."""
        return bool(metrics) and any(v not in (None, 0, "") for v in metrics.values())

    def _log_credential_gap_once(self, yt_key_present: bool, ig_token_present: bool) -> None:
        """One actionable log line per run when metrics fetches are guaranteed to come back empty."""
        if not yt_key_present:
            has_oauth = all((
                getattr(settings, "YT_REFRESH_TOKEN", ""),
                getattr(settings, "YT_CLIENT_ID", ""),
                getattr(settings, "YT_CLIENT_SECRET", ""),
            ))
            if not has_oauth:
                log.error(
                    "YouTube analytics will stay EMPTY: set the YT_API_KEY secret "
                    "(or all of YT_CLIENT_ID/YT_CLIENT_SECRET/YT_REFRESH_TOKEN) in repo secrets."
                )
        if not ig_token_present:
            log.error(
                "Instagram analytics will stay EMPTY: set the INSTAGRAM_ACCESS_TOKEN "
                "(or META_ACCESS_TOKEN) and INSTAGRAM_USER_ID secrets."
            )

    def load_deprecated_patterns(self) -> list[str]:
        """Load list of deprecated hook formulas and underperforming angles."""
        if not self.deprecated_path.is_file():
            return []
        try:
            with open(self.deprecated_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as exc:
            log.warning("Could not read deprecated patterns: %s", exc)
            return []

    def record_deprecated_pattern(self, pattern: str, reason: str) -> None:
        """Add an underperforming pattern to the deprecation list."""
        patterns = self.load_deprecated_patterns()
        clean = pattern.strip()
        if not clean or clean in patterns:
            return

        patterns.append(clean)
        # Limit to 30 most recent deprecated patterns to keep prompt focused
        if len(patterns) > 30:
            patterns = patterns[-30:]

        try:
            with open(self.deprecated_path, "w", encoding="utf-8") as f:
                json.dump(patterns, f, indent=2, ensure_ascii=False)
            log.info("🚫 Added to deprecated patterns: '%s' (Reason: %s)", clean[:60], reason)
        except Exception as exc:
            log.error("Failed to write deprecated patterns: %s", exc)

    def audit_recent_posts(self) -> dict:
        """
        Scan publish ledger for posts published 48h–14d ago that need auditing.
        Returns summary of audited posts and flagged patterns.
        """
        if not self.ledger_path.is_file():
            return {"audited": 0, "deprecated_added": 0}

        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except Exception as exc:
            log.error("Failed to load publish ledger for audit: %s", exc)
            return {"audited": 0, "deprecated_added": 0}

        now = datetime.now(timezone.utc)
        min_age = timedelta(hours=40)  # Eligible once ~40-48h old
        max_age = timedelta(days=14)

        audited_count = 0
        retried_count = 0
        failed_count = 0
        deprecated_count = 0
        state_changed = False
        analytics_records = self._load_analytics_records()

        self._log_credential_gap_once(
            yt_key_present=bool(getattr(settings, "YT_API_KEY", "").strip())
            or all((getattr(settings, "YT_REFRESH_TOKEN", ""), getattr(settings, "YT_CLIENT_ID", ""), getattr(settings, "YT_CLIENT_SECRET", ""))),
            ig_token_present=bool(
                (getattr(settings, "INSTAGRAM_ACCESS_TOKEN", "") or getattr(settings, "META_ACCESS_TOKEN", "")).strip()
            ),
        )

        for entry in ledger:
            if entry.get("audited"):
                continue

            ts_str = entry.get("timestamp")
            if not ts_str:
                continue

            try:
                post_dt = datetime.fromisoformat(ts_str)
                if post_dt.tzinfo is None:
                    post_dt = post_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            age = now - post_dt
            if age < min_age or age > max_age:
                continue

            post_title = entry.get("title", "Untitled")
            hook = entry.get("hook", "")
            topic = entry.get("topic", "")
            duration = float(entry.get("duration_seconds") or 25.0)
            platform_ids = entry.get("ids", {})

            log.info("Auditing 48h performance for: '%s'...", post_title[:50])

            ig_metrics = self._fetch_instagram_metrics(platform_ids.get("instagram"))
            yt_metrics = self._fetch_youtube_metrics(platform_ids.get("youtube"))

            # Never record an empty audit: if both platforms returned nothing, the
            # fetch failed (bad/missing secret, quota, network) and the tuner would
            # learn from a row of zeros. Retry on the next pipeline run instead.
            if not self._has_metrics(ig_metrics) and not self._has_metrics(yt_metrics):
                attempts = int(entry.get("audit_attempts", 0)) + 1
                entry["audit_attempts"] = attempts
                state_changed = True
                if attempts < MAX_AUDIT_ATTEMPTS:
                    retried_count += 1
                    log.warning(
                        "Metrics fetch came back empty for '%s' (attempt %d/%d). Will retry next run - NOT recording zeros.",
                        post_title[:50], attempts, MAX_AUDIT_ATTEMPTS,
                    )
                    continue
                failed_count += 1
                log.error(
                    "Giving up on '%s' after %d empty fetches. Recording fetch_failed so the gap is visible.",
                    post_title[:50], attempts,
                )
                entry["audited"] = True
                analytics_records.append({
                    "timestamp_audited": now.isoformat(),
                    "post_timestamp": ts_str,
                    "title": post_title,
                    "hook": hook,
                    "topic": topic,
                    "duration_seconds": duration,
                    "status": "fetch_failed",
                    "instagram": {},
                    "youtube": {},
                })
                continue

            record = {
                "timestamp_audited": now.isoformat(),
                "post_timestamp": ts_str,
                "title": post_title,
                "hook": hook,
                "topic": topic,
                "duration_seconds": duration,
                "status": "ok",
                "instagram": ig_metrics,
                "youtube": yt_metrics,
            }
            analytics_records.append(record)
            entry["audited"] = True
            state_changed = True
            audited_count += 1

            # Performance gate evaluation
            # 1. Check Instagram watch time / APV
            avg_watch_time = ig_metrics.get("avg_watch_time", 0.0)
            if duration > 0 and avg_watch_time > 0:
                apv = (avg_watch_time / duration) * 100.0
                if apv < 50.0 and hook:
                    self.record_deprecated_pattern(
                        hook,
                        f"Instagram APV was {apv:.1f}% (< 50% target threshold)"
                    )
                    deprecated_count += 1

            # 2. Check YouTube view velocity if available
            yt_views = yt_metrics.get("views", 0)
            if 0 < yt_views < 30 and hook:
                self.record_deprecated_pattern(
                    hook,
                    f"YouTube views remained below 30 after 48h ({yt_views} views)"
                )
                deprecated_count += 1

        # Save updated ledger with audited flags / retry counters
        if state_changed:
            try:
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    json.dump(ledger, f, indent=2, ensure_ascii=False)
                self._save_analytics_records(analytics_records)
                log.info(
                    "✓ Completed 48h audit: %d posts analyzed, %d retrying, %d fetch-failed, %d patterns deprecated.",
                    audited_count, retried_count, failed_count, deprecated_count,
                )
            except Exception as exc:
                log.error("Failed to save audit results: %s", exc)

        return {
            "audited": audited_count,
            "retrying": retried_count,
            "fetch_failed": failed_count,
            "deprecated_added": deprecated_count,
        }

    def _load_analytics_records(self) -> list[dict]:
        if not self.analytics_path.is_file():
            return []
        try:
            with open(self.analytics_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_analytics_records(self, records: list[dict]) -> None:
        try:
            with open(self.analytics_path, "w", encoding="utf-8") as f:
                json.dump(records[-200:], f, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.warning("Could not save analytics records: %s", exc)

    def _fetch_instagram_metrics(self, media_id: Optional[str]) -> dict:
        """Fetch Instagram reel insights via Meta Graph API."""
        if not media_id:
            return {}

        token = getattr(settings, "INSTAGRAM_ACCESS_TOKEN", "").strip() or getattr(settings, "META_ACCESS_TOKEN", "").strip()
        if not token:
            return {}

        clean_media_id = str(media_id).strip("/").split("/")[-1].split("?")[0]
        version = getattr(settings, "INSTAGRAM_GRAPH_VERSION", "v21.0")
        user_id = getattr(settings, "INSTAGRAM_USER_ID", "").strip() or os.environ.get("INSTAGRAM_USER_ID", "").strip()

        # If clean_media_id is a shortcode (non-digit) and user_id is set, resolve numeric ID via recent media list
        numeric_id = clean_media_id
        if not clean_media_id.isdigit() and user_id:
            try:
                list_url = f"https://graph.facebook.com/{version}/{user_id}/media"
                res = requests.get(list_url, params={"fields": "id,shortcode,permalink", "limit": 25, "access_token": token}, timeout=10)
                if res.status_code == 200:
                    for item in res.json().get("data", []):
                        if item.get("shortcode") == clean_media_id or clean_media_id in item.get("permalink", ""):
                            numeric_id = item.get("id")
                            break
            except Exception as e:
                log.debug("Failed to resolve Instagram shortcode %s: %s", clean_media_id, e)

        url = f"https://graph.facebook.com/{version}/{numeric_id}/insights"
        params = {
            "metric": "reach,saved,shares,total_interactions",
            "access_token": token,
        }

        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json().get("data", [])
                metrics = {}
                for m in data:
                    name = m.get("name")
                    values = m.get("values", [{}])
                    val = values[0].get("value", 0) if values else 0
                    metrics[name] = val
                log.info("✓ Meta Graph API insights for %s: %s", numeric_id, metrics)
                return metrics
            else:
                log.warning("Instagram insights for %s returned HTTP %d: %s", numeric_id, res.status_code, res.text[:150])
        except Exception as exc:
            log.warning("Instagram insights request failed for %s: %s", numeric_id, exc)

        return {}

    def _fetch_youtube_metrics(self, video_id: Optional[str]) -> dict:
        """Fetch YouTube short statistics via YouTube Data API v3 (API key or OAuth)."""
        if not video_id:
            return {}

        # Strip URL wrapper if full URL was stored
        clean_id = video_id
        if "youtu.be/" in video_id:
            clean_id = video_id.split("youtu.be/")[1].split("?")[0]
        elif "shorts/" in video_id:
            clean_id = video_id.split("shorts/")[1].split("?")[0]
        elif "v=" in video_id:
            clean_id = video_id.split("v=")[1].split("&")[0]

        def _stats_from(items):
            if not items:
                return {}
            stats = items[0].get("statistics", {})
            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "shares": 0,  # Not provided in base statistics
            }

        # OAuth first: the channel-owner token that uploads videos is the
        # maintained credential. The YT_API_KEY path has 401'd for months
        # (key restrictions/API enablement), leaving analytics blind.
        refresh_token = getattr(settings, "YT_REFRESH_TOKEN", "") or os.environ.get("YT_REFRESH_TOKEN", "")
        client_id = getattr(settings, "YT_CLIENT_ID", "") or os.environ.get("YT_CLIENT_ID", "")
        client_secret = getattr(settings, "YT_CLIENT_SECRET", "") or os.environ.get("YT_CLIENT_SECRET", "")
        if all((refresh_token, client_id, client_secret)):
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                creds = Credentials(
                    token=None,
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                )
                yt_service = build("youtube", "v3", credentials=creds, cache_discovery=False)
                res = yt_service.videos().list(part="statistics", id=clean_id).execute()
                metrics = _stats_from(res.get("items", []))
                if metrics:
                    log.info("✓ YouTube statistics via OAuth for %s: %s", clean_id, metrics)
                    # Retention curves need the YouTube Analytics API
                    # (yt-analytics.readonly scope). Fail-soft: older tokens
                    # simply skip this block until consent is refreshed.
                    try:
                        yta = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
                        today = datetime.now(timezone.utc)
                        rep = yta.reports().query(
                            ids="channel==MINE",
                            filters=f"video=={clean_id}",
                            startDate=(today - timedelta(days=28)).strftime("%Y-%m-%d"),
                            endDate=today.strftime("%Y-%m-%d"),
                            metrics="views,averageViewDuration,averageViewPercentage",
                            dimensions="video",
                        ).execute()
                        rows = rep.get("rows", [])
                        if rows:
                            metrics["avg_view_duration_s"] = rows[0][1]
                            metrics["avg_view_percentage"] = rows[0][2]
                            log.info("✓ Retention for %s: avgViewPercentage=%s", clean_id, rows[0][2])
                    except Exception as ret_exc:
                        log.info(
                            "Retention metrics unavailable for %s (token needs the "
                            "yt-analytics.readonly scope): %s", clean_id, ret_exc,
                        )
                    return metrics
            except Exception as oauth_exc:
                # Was log.debug: this failure silently kept analytics empty.
                # A 403 here means the OAuth consent scopes lack
                # youtube.readonly - re-authorize the channel token with it.
                log.error(
                    "YouTube statistics OAuth request failed for %s: %s "
                    "(if 403 insufficientPermissions, the channel token needs "
                    "the youtube.readonly scope - re-run OAuth consent)",
                    clean_id, oauth_exc,
                )

        # Fallback: API key (public statistics only).
        yt_key = getattr(settings, "YT_API_KEY", "").strip()
        if yt_key:
            url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                "part": "statistics",
                "id": clean_id,
                "key": yt_key,
            }
            try:
                res = requests.get(url, params=params, timeout=10)
                if res.status_code == 200:
                    metrics = _stats_from(res.json().get("items", []))
                    if metrics:
                        return metrics
                else:
                    log.error(
                        "YouTube API key request failed with status %s: %s "
                        "(check the key's restrictions and that YouTube Data "
                        "API v3 is enabled on its Google Cloud project)",
                        res.status_code, res.text,
                    )
            except Exception as e:
                log.error("YouTube metrics fetch error: %s", e)

        return {}
