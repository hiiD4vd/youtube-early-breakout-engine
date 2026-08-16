"""Early metadata-burst detection over already-observed Market Shorts."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketMetadataTrend, MarketMetadataTrendMembership, MarketMetadataTrendSnapshot, MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:market-metadata-bursts"
STOP = {"shorts", "short", "youtube", "viral", "viralshorts", "youtubeshorts", "fyp", "trending", "memes", "edit", "the", "and", "for", "with", "this", "that", "when", "what", "your", "they", "from", "video", "official", "copyright", "fair", "use", "football", "soccer", "sports", "funny", "comedy", "yang", "dan", "ini", "itu", "dari", "untuk", "langsung"}

def _terms(video: MarketVideo) -> set[tuple[str, str]]:
    text = " ".join(filter(None, [video.title, video.description]))
    hashtags = {tag.casefold() for tag in re.findall(r"#([\w]+)", text) if len(tag) >= 4}
    # Only title tokens written as names/acronyms become entity candidates.
    # Generic words at the start of a sentence are filtered explicitly above.
    words = {word.casefold() for word in re.findall(r"[\w']+", video.title or "") if len(word) >= 4 and (word[:1].isupper() or word.isupper())}
    return {(f"hashtag:{tag}", "HASHTAG") for tag in hashtags if tag not in STOP} | {(f"entity:{word}", "ENTITY") for word in words if word not in STOP}

@celery_app.task(name="app.tasks.market_metadata_tasks.detect_market_metadata_bursts")
def detect_market_metadata_bursts() -> dict[str, int | str]:
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=840): return {"status": "skipped_locked"}
    try:
      with SessionLocal() as db:
        now = datetime.now(UTC); cutoff = now - timedelta(hours=settings.market_metadata_window_hours)
        videos = db.scalars(select(MarketVideo).where(MarketVideo.shorts_status == "VERIFIED_SHORTS", MarketVideo.published_at >= cutoff)).all()
        latest: dict[int, MarketVideoObservation] = {}
        for observation in db.scalars(select(MarketVideoObservation).order_by(desc(MarketVideoObservation.observed_at))).all(): latest.setdefault(observation.market_video_id, observation)
        grouped: dict[tuple[str, str], list[MarketVideo]] = defaultdict(list)
        for video in videos:
          for key, kind in _terms(video): grouped[(key, kind)].append(video)
        existing = {trend.signal_key: trend for trend in db.scalars(select(MarketMetadataTrend)).all()}
        created = snapshots = 0
        for (key, kind), members in grouped.items():
          channels = {video.channel_id or video.video_id for video in members}
          if len(members) < 3 or len(channels) < 3: continue
          regions = {latest[video.id].region for video in members if video.id in latest and latest[video.id].region}
          fresh = sum(1 for video in members if video.published_at and video.published_at >= now - timedelta(hours=6)) / len(members)
          previous = db.scalar(select(MarketMetadataTrendSnapshot).join(MarketMetadataTrend, MarketMetadataTrend.id == MarketMetadataTrendSnapshot.market_metadata_trend_id).where(MarketMetadataTrend.signal_key == key).order_by(desc(MarketMetadataTrendSnapshot.observed_at)))
          new_members = len(members) if not previous else max(0, len(members) - previous.member_count)
          score = round(100 * (0.35 * min(1, len(channels)/6) + 0.25 * min(1, len(regions)/3) + 0.25 * fresh + 0.15 * min(1, new_members/3)), 2)
          trend = existing.get(key)
          if not trend:
            trend = MarketMetadataTrend(signal_key=key, label=key.split(":", 1)[1].title(), signal_type=kind); db.add(trend); db.flush(); existing[key] = trend; created += 1
          trend.member_count, trend.channel_count, trend.region_count, trend.fresh_ratio, trend.burst_score, trend.last_observed_at = len(members), len(channels), len(regions), round(fresh, 3), score, now
          # Metadata alone is private evidence. It becomes public only after
          # the cluster-level semantic naming task marks it followable.
          trend.status = "WATCHING"
          known = set(db.scalars(select(MarketMetadataTrendMembership.market_video_id).where(MarketMetadataTrendMembership.market_metadata_trend_id == trend.id)).all())
          for video in members:
            if video.id not in known: db.add(MarketMetadataTrendMembership(market_metadata_trend_id=trend.id, market_video_id=video.id, matched_term=key))
          db.add(MarketMetadataTrendSnapshot(market_metadata_trend_id=trend.id, observed_at=now, member_count=len(members), channel_count=len(channels), region_count=len(regions), fresh_ratio=fresh, burst_score=score)); snapshots += 1
        db.commit()
      store.set_status(market_metadata_burst_last_run_at=datetime.now(UTC).isoformat(), market_metadata_burst_snapshots=snapshots)
      return {"created": created, "snapshots": snapshots}
    finally: store.client.delete(LOCK)
