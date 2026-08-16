"""Safe Apify boundary: normalize third-party records before pipeline intake.

Actor schemas vary. This adapter deliberately accepts only a small common
subset and drops rows without a stable YouTube video id. No record here can
write to a ranking directly.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.services.market_source_contract import ObservedShort


_VIDEO_ID = re.compile(r"(?:shorts/|watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")


def _text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _integer(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.replace(",", "").isdigit():
            return int(value.replace(",", ""))
    return None


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def normalize_apify_short(row: dict[str, Any], *, region: str | None, observed_at: datetime | None = None) -> ObservedShort | None:
    # Streamers' Actor explicitly returns `type: shorts`. Respect that signal
    # before metadata verification; a landscape or regular video can never
    # enter the Shorts topic candidate path just because it is short in time.
    row_type = row.get("type")
    # This adapter is exclusively for the Streamers Shorts collection lane.
    # Fail closed if the provider does not positively identify it as a Short;
    # that is safer than relying on duration and accidentally accepting a
    # landscape regular video.
    if not isinstance(row_type, str) or row_type.strip().casefold() not in {"short", "shorts"}:
        return None
    url = _text(row, "url", "videoUrl", "webpage_url")
    video_id = _text(row, "videoId", "video_id", "id")
    if not video_id and url:
        match = _VIDEO_ID.search(url)
        video_id = match.group(1) if match else None
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    video_url = url or f"https://www.youtube.com/shorts/{video_id}"
    return ObservedShort(
        source_lane="apify", source_region=region, video_id=video_id,
        title=_text(row, "title", "videoTitle"),
        channel_id=_text(row, "channelId", "channel_id", "authorId"),
        channel_title=_text(row, "channelName", "channelTitle", "author"),
        published_at=_time(_text(row, "publishedAt", "uploadDate", "published_at", "date")),
        observed_at=observed_at or datetime.now(UTC),
        view_count=_integer(row, "viewCount", "views", "view_count"),
        thumbnail_url=_text(row, "thumbnailUrl", "thumbnail", "thumbnail_url"),
        video_url=video_url,
    )
