"""Direct anonymous Shorts-feed intake for Market Trends."""

from datetime import UTC, datetime

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.services.youtube_client import YoutubeAnonymousClient
from app.tasks.celery_app import celery_app

MARKET_FEED_LOCK = "ycgc:youtube:lock:market-shorts-feed"


@celery_app.task(bind=True, name="app.tasks.market_feed_tasks.collect_market_shorts_feed", soft_time_limit=300, time_limit=360)
def collect_market_shorts_feed(self: Task) -> dict[str, int | str]:
    """This lane is Shorts by construction: it reads logged-out Shorts reel feed."""
    store = SeedStore()
    if not store.client.set(MARKET_FEED_LOCK, "1", nx=True, ex=330):
        return {"status": "skipped_locked"}
    created = updated = observations = 0
    now = datetime.now(UTC)
    try:
        with SessionLocal() as db:
            existing = {item.video_id: item for item in db.scalars(select(MarketVideo)).all()}
            for region, language in settings.youtube_profile_list:
                client = YoutubeAnonymousClient(region=region, language=language)
                try:
                    seeds, _ = client.discover_seeds(max_pages=settings.youtube_seed_pages_per_session, max_accepted=settings.youtube_seed_limit_per_session)
                finally:
                    client.close()
                for rank, seed in enumerate(seeds, start=1):
                    video = existing.get(seed.video_id)
                    if not video:
                        video = MarketVideo(video_id=seed.video_id, video_url=seed.video_url)
                        db.add(video); db.flush(); existing[seed.video_id] = video; created += 1
                    else:
                        updated += 1
                    video.channel_id, video.channel_title, video.title = seed.channel_id, seed.channel_title, seed.title
                    video.thumbnail_url, video.published_at = seed.thumbnail_url, seed.published_at
                    video.last_seen_at, video.shorts_status = now, "VERIFIED_SHORTS"
                    video.source_provenance = {"first_lane": "anonymous_shorts_feed", "shorts_verified_by_source": True}
                    db.add(MarketVideoObservation(market_video_id=video.id, observed_at=now, source_lane="anonymous_shorts_feed", region=region, language=language, view_count=seed.seed_view_count, source_rank=rank))
                    observations += 1
            db.commit()
        store.set_status(market_feed_state="ok", market_feed_last_scan_at=now.isoformat(), market_feed_created=created, market_feed_observations=observations)
        return {"created": created, "updated": updated, "observations": observations}
    finally:
        store.client.delete(MARKET_FEED_LOCK)
