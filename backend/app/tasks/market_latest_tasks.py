"""Official, keyword-free latest-video intake for Market Trends.

This endpoint is an observed sample, not a claim to be YouTube's complete
upload firehose.  Each enabled region is queried independently so adding a
region increases coverage instead of splitting an existing region's quota.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app
from app.tasks.market_trends_tasks import _duration_seconds, _int, _parse_time

LOCK = "ycgc:youtube:lock:market-latest"
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


@celery_app.task(bind=True, name="app.tasks.market_latest_tasks.collect_market_latest")
def collect_market_latest(self: Task) -> dict[str, int | str]:
    """Collect a neutral *latest* sample, then leave format verification to yt-dlp."""
    store = SeedStore()
    if not settings.market_latest_enabled:
        return {"status": "disabled"}
    if not settings.youtube_data_api_key:
        return {"status": "youtube_data_api_key_not_configured"}
    if not store.client.set(LOCK, "1", nx=True, ex=900):
        return {"status": "skipped_locked"}

    created = updated = observations = 0
    now = datetime.now(UTC)
    published_after = (now - timedelta(hours=settings.market_latest_window_hours)).isoformat().replace("+00:00", "Z")
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.youtube_http_timeout_seconds)) as client, SessionLocal() as db:
            existing = {video.video_id: video for video in db.scalars(select(MarketVideo)).all()}
            for region, language in settings.market_profile_list:
                # No q parameter: this collection never discovers through a
                # human topic/hashtag keyword. search.list is still a bounded
                # public API sample, which is disclosed in source provenance.
                response = client.get(SEARCH_URL, params={
                    "key": settings.youtube_data_api_key,
                    "part": "snippet",
                    "type": "video",
                    "order": "date",
                    "publishedAfter": published_after,
                    "regionCode": region,
                    "relevanceLanguage": language,
                    "videoDuration": "short",
                    "maxResults": settings.market_latest_results_per_region,
                })
                response.raise_for_status()
                ids = [item.get("id", {}).get("videoId") for item in response.json().get("items", [])]
                ids = [video_id for video_id in ids if video_id]
                if not ids:
                    continue
                details = client.get(VIDEOS_URL, params={
                    "key": settings.youtube_data_api_key,
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(ids),
                })
                details.raise_for_status()
                for rank, item in enumerate(details.json().get("items", []), start=1):
                    snippet, statistics, content = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
                    if _duration_seconds(content.get("duration")) > 180:
                        continue
                    video_id = item["id"]
                    video = existing.get(video_id)
                    if video is None:
                        video = MarketVideo(video_id=video_id, video_url=f"https://www.youtube.com/watch?v={video_id}")
                        db.add(video); db.flush(); existing[video_id] = video; created += 1
                    else:
                        updated += 1
                    video.channel_id = snippet.get("channelId")
                    video.channel_title = snippet.get("channelTitle")
                    video.title = snippet.get("title")
                    video.description = snippet.get("description")
                    video.thumbnail_url = (snippet.get("thumbnails", {}).get("high") or snippet.get("thumbnails", {}).get("default") or {}).get("url")
                    video.published_at = _parse_time(snippet.get("publishedAt"))
                    video.category_id = snippet.get("categoryId")
                    video.duration_iso8601 = content.get("duration")
                    if video.shorts_status not in {"VERIFIED_SHORTS", "REJECTED_NOT_SHORTS"}:
                        video.shorts_status = "SHORT_DURATION_CANDIDATE"
                    video.last_seen_at = now
                    provenance = dict(video.source_provenance or {})
                    provenance.update({"official": True, "latest_sample": True, "latest_sample_scope": "bounded_public_api"})
                    video.source_provenance = provenance
                    db.add(MarketVideoObservation(
                        market_video_id=video.id, observed_at=now, source_lane="official_latest_sample",
                        region=region, language=language, category_id=video.category_id,
                        view_count=_int(statistics.get("viewCount")) or 0,
                        like_count=_int(statistics.get("likeCount")), comment_count=_int(statistics.get("commentCount")),
                        source_rank=rank, raw_payload={"published_after": published_after},
                    ))
                    observations += 1
            db.commit()
        store.set_status(market_latest_state="ok", market_latest_last_scan_at=now.isoformat(), market_latest_created=created, market_latest_updated=updated, market_latest_observations=observations)
        return {"created": created, "updated": updated, "observations": observations}
    except httpx.HTTPError as exc:
        store.set_status(market_latest_state="source_error", market_latest_last_error=type(exc).__name__)
        raise self.retry(exc=exc, countdown=300, max_retries=1)
    finally:
        store.client.delete(LOCK)
