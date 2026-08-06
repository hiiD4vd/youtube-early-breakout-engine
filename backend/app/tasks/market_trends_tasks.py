"""Official public-chart collector for the isolated Market Trends lane."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

MARKET_LOCK = "ycgc:youtube:lock:market-trends"
MARKET_CHART_URL = "https://www.googleapis.com/youtube/v3/videos"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _int(value) -> int | None:
    return int(value) if value not in (None, "") else None


@celery_app.task(bind=True, name="app.tasks.market_trends_tasks.collect_market_chart")
def collect_market_chart(self: Task) -> dict[str, int | str]:
    """Collect broad public chart evidence; it is never fed into seed discovery."""
    store = SeedStore()
    if not settings.market_trends_enabled:
        return {"status": "disabled"}
    if not settings.youtube_data_api_key:
        store.set_status(market_trends_state="youtube_data_api_key_not_configured")
        return {"status": "youtube_data_api_key_not_configured"}
    if not store.client.set(MARKET_LOCK, "1", nx=True, ex=540):
        return {"status": "skipped_locked"}
    created = updated = observations = 0
    now = datetime.now(UTC)
    regions = [item.strip().upper() for item in settings.market_trends_regions.split(",") if len(item.strip()) == 2]
    categories = [item.strip() for item in settings.market_trends_chart_categories.split(",") if item.strip()]
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.youtube_http_timeout_seconds)) as client, SessionLocal() as db:
            existing = {item.video_id: item for item in db.scalars(select(MarketVideo)).all()}
            for region in regions:
                for category in categories:
                    response = client.get(MARKET_CHART_URL, params={
                        "key": settings.youtube_data_api_key, "part": "snippet,statistics,contentDetails",
                        "chart": "mostPopular", "regionCode": region, "videoCategoryId": category,
                        "maxResults": settings.market_trends_max_results,
                    })
                    response.raise_for_status()
                    for rank, item in enumerate(response.json().get("items", []), start=1):
                        snippet, statistics, content = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
                        video_id = item["id"]
                        video = existing.get(video_id)
                        if not video:
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
                        video.last_seen_at = now
                        video.source_provenance = {"first_lane": "official_chart", "official": True}
                        db.add(MarketVideoObservation(market_video_id=video.id, observed_at=now, source_lane="official_chart", region=region, category_id=category, view_count=_int(statistics.get("viewCount")) or 0, like_count=_int(statistics.get("likeCount")), comment_count=_int(statistics.get("commentCount")), source_rank=rank, raw_payload={"etag": item.get("etag")}))
                        observations += 1
            db.commit()
        store.set_status(market_trends_state="ok", market_trends_last_scan_at=now.isoformat(), market_trends_created=created, market_trends_updated=updated, market_trends_observations=observations)
        return {"created": created, "updated": updated, "observations": observations}
    except httpx.HTTPError as exc:
        store.set_status(market_trends_state="source_error", market_trends_last_error=type(exc).__name__)
        raise self.retry(exc=exc, countdown=120, max_retries=2)
    finally:
        store.client.delete(MARKET_LOCK)
