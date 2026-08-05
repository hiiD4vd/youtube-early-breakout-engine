"""Post-discovery channel context. It never participates in seed selection."""

from __future__ import annotations

import json
import statistics
import subprocess
from typing import Any

from app.config import settings


def _run_ytdlp(url: str, flat: bool = False) -> list[dict[str, Any]]:
    args = ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", "--playlist-end", str(settings.channel_context_history_limit)]
    if flat:
        args.append("--flat-playlist")
    completed = subprocess.run(args + [url], check=True, capture_output=True, text=True, timeout=120)
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def classify_channel(subscribers: int | None, historical_views: list[int]) -> tuple[str, float, str]:
    median_views = int(statistics.median(historical_views)) if historical_views else None
    evidence = int(subscribers is not None) + int(len(historical_views) >= 5)
    if (subscribers is not None and subscribers >= settings.whale_subscriber_threshold) or (median_views is not None and median_views >= settings.whale_median_view_threshold):
        return "WHALE", min(0.95, 0.55 + 0.2 * evidence), "high established audience or consistently high historical views"
    if (subscribers is not None and subscribers >= settings.established_subscriber_threshold) or (median_views is not None and median_views >= settings.established_median_view_threshold):
        return "ESTABLISHED", min(0.9, 0.45 + 0.2 * evidence), "established channel context"
    if evidence == 2:
        return "UNDERDOG", 0.8, "small channel context with no consistently large recent view history"
    return "UNKNOWN", 0.25 + 0.15 * evidence, "insufficient public channel evidence"


def fetch_channel_context(channel_id: str, video_url: str) -> dict[str, Any]:
    """Use public video/channel pages only; optional API keys are not required."""
    try:
        video = _run_ytdlp(video_url)[0]
        subscribers = video.get("channel_follower_count")
        subscribers = int(subscribers) if isinstance(subscribers, (int, float)) else None
        history = _run_ytdlp(f"https://www.youtube.com/channel/{channel_id}/videos", flat=True)
        views = [int(item["view_count"]) for item in history if isinstance(item.get("view_count"), (int, float)) and item["view_count"] >= 0]
        classification, confidence, reason = classify_channel(subscribers, views)
        median_views = int(statistics.median(views)) if views else None
        return {"status": classification, "confidence": confidence, "reason": reason, "subscriber_count": subscribers, "history_sample_size": len(views), "median_recent_views": median_views, "source": "public_ytdlp", "gold_candidate": classification == "UNDERDOG"}
    except (subprocess.SubprocessError, json.JSONDecodeError, IndexError, KeyError) as exc:
        return {"status": "UNKNOWN", "confidence": 0.0, "reason": "public channel context unavailable", "source": "public_ytdlp", "error": str(exc)[:180], "gold_candidate": False}
