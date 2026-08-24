"""Merge duplicate market topics that share the same normalised label.

The clusterer seeds topic labels from per-video LLM themes. Two batches can
produce two topics with the identical label ("brazilian funk music" twice),
which fragments evidence and pollutes leaderboards. This task deterministically
merges such twins without touching topic structure (no re-clustering).
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models.market_trends import MarketTopic, MarketTopicMembership, MarketVideo
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

# Shared with cluster_market_topics so the two never mutate memberships at the
# same time.
TOPIC_MUTATION_LOCK = "ycgc:youtube:lock:market-topic-membership-mutation"
MERGE_LOCK = "ycgc:youtube:lock:market-topic-dedupe"


def _normalise_label(label: str) -> str:
    lowered = (label or "").strip().lower()
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    # Singular/plural and music/video suffix variants collapse to one key:
    # "brazilian funk music" vs "brazilian funk music videos" are the same
    # trend to a human reader.
    stop_suffixes = (
        "music videos", "music video", "music", "videos", "video",
        "shorts", "short", "clips", "clip", "content",
    )
    tokens = lowered.split()
    while len(tokens) > 2 and " ".join(tokens[-2:]) in stop_suffixes:
        tokens = tokens[:-2]
    while len(tokens) > 1 and tokens[-1] in stop_suffixes:
        tokens = tokens[:-1]
    return " ".join(tokens).strip()


@celery_app.task(name="app.tasks.market_topic_dedupe_tasks.merge_duplicate_market_topics")
def merge_duplicate_market_topics() -> dict[str, int | str]:
    """Fold same-label topics into their oldest survivor."""
    store = SeedStore()
    if not store.client.set(MERGE_LOCK, "1", nx=True, ex=540):
        return {"status": "skipped_locked"}
    try:
        with SessionLocal() as db:
            topics = db.scalars(
                select(MarketTopic).where(MarketTopic.status != "MERGED")
            ).all()

            by_norm: dict[str, list[MarketTopic]] = defaultdict(list)
            for topic in topics:
                key = _normalise_label(topic.label)
                if len(key) >= 4:  # skip degenerate one-word leftovers
                    by_norm[key].append(topic)

            merged = 0
            moved = 0
            survivor_ids: list[int] = []
            for key, twins in by_norm.items():
                if len(twins) < 2:
                    continue
                # Oldest, biggest topic survives; ties break on id.
                twins.sort(key=lambda t: (-(t.member_count or 0), t.id))
                survivor = twins[0]
                for duplicate in twins[1:]:
                    rows = db.scalars(
                        select(MarketTopicMembership).where(
                            MarketTopicMembership.market_topic_id == duplicate.id
                        )
                    ).all()
                    for row in rows:
                        # One video may belong to only one topic (unique
                        # constraint). Move when free, drop when the survivor
                        # already holds it.
                        exists = db.scalar(
                            select(MarketTopicMembership.id).where(
                                MarketTopicMembership.market_topic_id == survivor.id,
                                MarketTopicMembership.market_video_id == row.market_video_id,
                            )
                        )
                        if exists:
                            db.delete(row)
                        else:
                            row.market_topic_id = survivor.id
                            moved += 1
                    duplicate.status = "MERGED"
                    duplicate.member_count = 0
                    duplicate.channel_count = 0
                    merged += 1
                survivor_ids.append(survivor.id)

            # Recount survivors from durable memberships only.
            for survivor_id in survivor_ids:
                topic = db.get(MarketTopic, survivor_id)
                if topic is None:
                    continue
                members = db.scalars(
                    select(MarketTopicMembership.market_video_id).where(
                        MarketTopicMembership.market_topic_id == survivor_id
                    )
                ).all()
                channels = db.scalars(
                    select(MarketVideo.channel_id)
                    .join(MarketTopicMembership, MarketTopicMembership.market_video_id == MarketVideo.id)
                    .where(MarketTopicMembership.market_topic_id == survivor_id)
                ).all()
                topic.member_count = len(members)
                topic.channel_count = len({c for c in channels if c})
                topic.status = "EMERGING" if topic.member_count >= 2 and topic.channel_count >= 2 else "PRIVATE_CANDIDATE"

            db.commit()
            return {"status": "ok", "merged": merged, "memberships_moved": moved}
    finally:
        store.client.delete(MERGE_LOCK)
