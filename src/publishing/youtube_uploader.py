"""
src/publishing/youtube_uploader.py

YouTube Data API v3 uploader (OAuth2 refresh token flow).
Disabled by default. Set ENABLE_YT_UPLOAD=true and ALLOW_PUBLICATION=true to activate.

The upload package includes:
  - Auto-generated SEO title (from Groq)
  - Auto-generated description + hashtags (from script metadata)
  - Category: 22 (People & Blogs)
  - Privacy: public (configurable)
  - Made for kids: false
  - Tags from hashtags list
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.youtube_titles import normalize_youtube_title

log = get_logger(__name__, phase="youtube_upload")

_DESCRIPTION_FOOTER = (
    "💬 Comment 'GUIDE' below for the complete breakdown & checklist!\n\n"
    "Subscribe for daily market myth-busting.\n\n"
    "Ask your market doubts on Telegram:\n"
    "English: https://t.me/MarketDebunk\n"
    "Tamil: https://t.me/marketdebunk_tamil"
)


def _get_authenticated_service():
    """Build an authenticated YouTube API client using OAuth2 refresh token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=settings.YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.YT_CLIENT_ID,
        client_secret=settings.YT_CLIENT_SECRET,
    )
    service = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return service


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    hashtags: list[str],
    privacy: str = "public",
) -> Optional[str]:
    """
    Upload distribution_ready.mp4 to YouTube.

    Returns the YouTube video ID on success, None if upload is disabled.
    """
    if not settings.ENABLE_YT_UPLOAD:
        log.info("YouTube upload is disabled (ENABLE_YT_UPLOAD=false) — skipping")
        return None
    if not settings.ALLOW_PUBLICATION:
        log.info("YouTube upload is locked (ALLOW_PUBLICATION=false) — skipping")
        return None

    if not all([settings.YT_CLIENT_ID, settings.YT_CLIENT_SECRET, settings.YT_REFRESH_TOKEN]):
        log.warning("YouTube OAuth credentials missing — upload skipped")
        return None

    title = normalize_youtube_title(title)
    log.info("Uploading to YouTube: '%s'", title)

    try:
        from googleapiclient.http import MediaFileUpload

        youtube = _get_authenticated_service()

        # Build tags from hashtags (strip # prefix)
        tags = [h.lstrip("#") for h in hashtags][:15]

        # Full description with CTA, Telegram links, and hashtags appended.
        full_description = f"{description}\n\n{_DESCRIPTION_FOOTER}\n\n" + " ".join(f"#{t}" for t in tags)

        request_body = {
            "snippet": {
                "title": title[:100],           # YouTube max 100 chars
                "description": full_description[:5000],
                "tags": tags,
                "categoryId": "27",             # Education
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
                # YouTube requires disclosing meaningfully synthetic media;
                # every video here is AI-scripted with a synthetic TTS voice.
                "containsSyntheticMedia": True,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=5 * 1024 * 1024,  # 5 MB chunks
        )

        upload_request = youtube.videos().insert(
            part=",".join(request_body.keys()),
            body=request_body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = upload_request.next_chunk()
            if status:
                log.info("Upload progress: %.0f%%", status.progress() * 100)

        video_id = response.get("id", "")
        log.info("✓ Uploaded! YouTube video ID: %s", video_id)
        log.info("  URL: https://www.youtube.com/shorts/%s", video_id)
        return video_id

    except Exception as exc:
        log.error("YouTube upload failed: %s", exc)
        return None
