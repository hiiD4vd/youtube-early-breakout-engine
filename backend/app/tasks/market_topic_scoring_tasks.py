"""Score Market Topics from observed public-chart evidence only."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy import desc, select

from app.database import SessionLocal
from app.models.market_trends import (
    MarketTopic,
    MarketTopicMembership,
    MarketTopicSnapshot,
    MarketVideo,
    MarketVideoObservation,
)
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app


LOCK = "ycgc:youtube:lock:market-topic-score"
SCORING_VERSION = "market-topic-v1"


def _score(member_count: int, channel_count: int, velocity: float, acceleration: float | None, new_members: int) -> float:
    evidence = min(1.0, member_count / 5)
    diversity = min(1.0, channel_count / 4)
    pace = min(1.0, math.log1p(max(0.0, velocity)) / math.log1p(100_000))
    growth = min(1.0, new_members / 3)
    acceleration_score = min(1.0, max(0.0, acceleration or 0.0))
    return round(100 * (0.30 * evidence + 0.30 * diversity + 0.25 * pace + 0.10 * growth + 0.05 * acceleration_score), 2)


def _status(member_count: int, channel_count: int, score: float, acceleration: float | None) -> str:
    if member_count < 2 or channel_count < 2:
        return "PRIVATE_CANDIDATE"
    if member_count >= 4 and channel_count >= 3 and score >= 60:
        return "CONFIRMED"
    if member_count >= 3 and channel_count >= 2 and score >= 38 and (acceleration or 0) >= 0:
        return "ACCELERATING"
    return "EMERGING"


@celery_app.task(name="app.tasks.market_topic_scoring_tasks.score_market_topics")
def score_market_topics() -> dict[str, int | str]:
    """Create a timestamped, explainable score for every semantic Market Topic."""
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked"}
    snapshots = 0
    try:
        with SessionLocal() as db:
            topics = db.scalars(select(MarketTopic)).all()
            latest_observation: dict[int, MarketVideoObservation] = {}
            for observation in db.scalars(select(MarketVideoObservation).order_by(desc(MarketVideoObservation.observed_at))).all():
                latest_observation.setdefault(observation.market_video_id, observation)
            now = datetime.now(UTC)
            for topic in topics:
                videos = db.scalars(
                    select(MarketVideo)
                    .join(MarketTopicMembership, MarketTopicMembership.market_video_id == MarketVideo.id)
                    .where(MarketTopicMembership.market_topic_id == topic.id)
                ).all()
                if not videos:
                    continue
                current_views = sum((latest_observation.get(video.id).view_count if latest_observation.get(video.id) else 0) for video in videos)
                channels = {video.channel_id for video in videos if video.channel_id}
                previous = db.scalar(
                    select(MarketTopicSnapshot)
                    .where(MarketTopicSnapshot.market_topic_id == topic.id)
                    .order_by(desc(MarketTopicSnapshot.observed_at))
                )
                elapsed_hours = ((now - previous.observed_at).total_seconds() / 3600) if previous else 0
                velocity = max(0.0, (current_views - previous.observed_views) / elapsed_hours) if previous and elapsed_hours > 0 else 0.0
                acceleration = None if not previous or previous.observed_velocity_per_hour <= 0 else round((velocity - previous.observed_velocity_per_hour) / previous.observed_velocity_per_hour, 4)
                new_members = len(videos) if not previous else max(0, len(videos) - previous.member_count)
                score = _score(len(videos), len(channels), velocity, acceleration, new_members)
                topic.member_count = len(videos)
                topic.channel_count = len(channels)
                topic.observed_views = current_views
                topic.observed_velocity_per_hour = round(velocity, 2)
                topic.acceleration = acceleration
                topic.trend_score = score
                topic.status = "PROVISIONAL" if topic.label.endswith(" - title-overlap candidate") else _status(len(videos), len(channels), score, acceleration)
                topic.last_observed_at = now
                db.add(MarketTopicSnapshot(
                    market_topic_id=topic.id, observed_at=now, observed_views=current_views,
                    observed_velocity_per_hour=velocity, acceleration=acceleration,
                    member_count=len(videos), channel_count=len(channels), new_member_count=new_members,
                    trend_score=score, scoring_version=SCORING_VERSION,
                ))
                snapshots += 1
            db.commit()
        store.set_status(market_topic_score_last_run_at=datetime.now(UTC).isoformat(), market_topic_snapshots=snapshots)
        return {"snapshots": snapshots}
    finally:
        store.client.delete(LOCK)
