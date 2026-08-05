"""Post-discovery channel context. It never participates in seed selection."""

from __future__ import annotations

import json
import statistics
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from app.config import settings


def _run_ytdlp(url: str, flat: bool = False) -> list[dict[str, Any]]:
    args = ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", "--playlist-end", str(settings.channel_context_history_limit)]
    if flat:
        args.append("--flat-playlist")
    completed = subprocess.run(args + [url], check=True, capture_output=True, text=True, timeout=35)
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def _youtube_api_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    query = urlencode({**params, "key": settings.youtube_data_api_key})
    with urlopen(f"https://www.googleapis.com/youtube/v3/{path}?{query}", timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _official_channel_history(channel_id: str) -> tuple[int | None, list[int]]:
    """Bounded official fallback, only when the user configured their own API key."""
    channel = _youtube_api_json("channels", {"part": "statistics,contentDetails", "id": channel_id})["items"][0]
    subscribers = channel.get("statistics", {}).get("subscriberCount")
    uploads_id = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads_id:
        return int(subscribers) if subscribers is not None else None, []
    uploads = _youtube_api_json("playlistItems", {"part": "contentDetails", "playlistId": uploads_id, "maxResults": str(settings.channel_context_history_limit)})
    video_ids = [item.get("contentDetails", {}).get("videoId") for item in uploads.get("items", [])]
    video_ids = [video_id for video_id in video_ids if video_id]
    if not video_ids:
        return int(subscribers) if subscribers is not None else None, []
    videos = _youtube_api_json("videos", {"part": "statistics", "id": ",".join(video_ids)})
    views = [int(item["statistics"]["viewCount"]) for item in videos.get("items", []) if item.get("statistics", {}).get("viewCount", "").isdigit()]
    return int(subscribers) if subscribers is not None else None, views


def classify_channel(subscribers: int | None, historical_views: list[int]) -> tuple[str, float, str]:
    median_views = int(statistics.median(historical_views)) if historical_views else None
    evidence = int(subscribers is not None) + int(len(historical_views) >= 5)
    if (subscribers is not None and subscribers >= settings.whale_subscriber_threshold) or (median_views is not None and median_views >= settings.whale_median_view_threshold):
        return "WHALE", min(0.95, 0.55 + 0.2 * evidence), "high established audience or consistently high historical views"
    if median_views is not None and median_views >= settings.proven_winner_median_view_threshold and len(historical_views) >= 5:
        return "PROVEN_WINNER", min(0.9, 0.5 + 0.2 * evidence), "recent uploads already perform consistently at a very high level"
    if (subscribers is not None and subscribers >= settings.established_subscriber_threshold) or (median_views is not None and median_views >= settings.established_median_view_threshold):
        return "ESTABLISHED", min(0.9, 0.45 + 0.2 * evidence), "established channel context"
    if evidence == 2:
        return "UNDERDOG", 0.8, "small channel context with no consistently large recent view history"
    return "UNKNOWN", 0.25 + 0.15 * evidence, "insufficient public channel evidence"


def fetch_channel_context(channel_id: str, video_url: str) -> dict[str, Any]:
    """Use public video/channel pages only; optional API keys are not required."""
    if settings.youtube_data_api_key:
        try:
            subscribers, views = _official_channel_history(channel_id)
            classification, confidence, reason = classify_channel(subscribers, views)
            return {"status": classification, "confidence": confidence, "reason": reason, "subscriber_count": subscribers, "history_sample_size": len(views), "median_recent_views": int(statistics.median(views)) if views else None, "source": "youtube_data_api", "gold_candidate": classification == "UNDERDOG", "attempt_count": 1}
        except (IndexError, KeyError, OSError, ValueError, json.JSONDecodeError):
            # Keep the no-key public path available when quota or the API is temporarily unavailable.
            pass
    try:
        video = _run_ytdlp(video_url)[0]
        subscribers = video.get("channel_follower_count")
        subscribers = int(subscribers) if isinstance(subscribers, (int, float)) else None
        history_urls = [
            video.get("channel_url"), video.get("uploader_url"),
            f"https://www.youtube.com/@{video['uploader_id']}/shorts" if video.get("uploader_id") else None,
            f"https://www.youtube.com/channel/{channel_id}/shorts" if channel_id else None,
            f"https://www.youtube.com/channel/{channel_id}/videos" if channel_id else None,
        ]
        views: list[int] = []
        failures: list[str] = []
        for history_url in dict.fromkeys(url for url in history_urls if url):
            try:
                history = _run_ytdlp(history_url, flat=True)
                views = [int(item["view_count"]) for item in history if isinstance(item.get("view_count"), (int, float)) and item["view_count"] >= 0]
                if views:
                    break
            except subprocess.SubprocessError as exc:
                failures.append(str(exc)[:100])
        if not views:
            raise subprocess.SubprocessError("no public view history: " + " | ".join(failures[:2]))
        classification, confidence, reason = classify_channel(subscribers, views)
        median_views = int(statistics.median(views)) if views else None
        return {"status": classification, "confidence": confidence, "reason": reason, "subscriber_count": subscribers, "history_sample_size": len(views), "median_recent_views": median_views, "source": "public_ytdlp", "gold_candidate": classification == "UNDERDOG", "attempt_count": 1}
    except (subprocess.SubprocessError, json.JSONDecodeError, IndexError, KeyError) as exc:
        return {"status": "UNKNOWN", "confidence": 0.0, "reason": "public channel context temporarily unavailable", "source": "public_ytdlp", "error": str(exc)[:180], "gold_candidate": False, "attempt_count": 1, "next_retry_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat()}
