"""Transparent title-overlap topic candidates while semantic AI is unavailable."""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketTopic, MarketTopicMembership, MarketVideo
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app


# These are broad format/category words, not a subject worth presenting as a
# TikTok-style topic. A remaining repeated proper-name/topic word is explicit
# evidence, labelled provisional until Gemini confirms its semantic meaning.
STOP = {"the", "and", "for", "with", "this", "that", "from", "shorts", "short", "youtube", "video", "viral", "fyp", "trending", "funny", "comedy", "sports", "football", "soccer", "bola", "games", "game", "when", "what", "about", "your", "every", "best", "like", "over", "after", "always", "almost", "never", "everyone", "entire", "part", "time", "more", "great", "copyright", "fair", "use", "official", "www", "http", "https", "com", "yang", "dan", "ini", "itu", "dari", "untuk", "saat", "jadi", "orang", "bikin", "malah", "inilah", "punya", "baseball", "speed"}
LABEL_SUFFIX = " - title-overlap candidate"
LOCK = "ycgc:youtube:lock:market-topic-membership-mutation"


def _tokens(title: str | None) -> set[str]:
    return {token for token in re.findall(r"[\w']+", (title or "").casefold()) if len(token) >= 4 and token not in STOP and not token.isdigit()}


@celery_app.task(name="app.tasks.market_fallback_topics_tasks.build_title_overlap_candidates")
def build_title_overlap_candidates() -> dict[str, int]:
    """Create only independently repeated title subjects; no fabricated topic."""
    if not settings.market_title_overlap_fallback_enabled:
        return {"created": 0, "assigned": 0, "candidate_terms": 0, "status": "disabled"}
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=280):
        return {"created": 0, "assigned": 0, "candidate_terms": 0}
    try:
      with SessionLocal() as db:
        # Remove only empty, derived fallback records left by an interrupted
        # concurrent run. Raw videos and all observations remain untouched.
        empty_topics = db.scalars(
            select(MarketTopic).where(MarketTopic.status == "PROVISIONAL")
        ).all()
        for topic in empty_topics:
            has_member = db.scalar(select(MarketTopicMembership.id).where(MarketTopicMembership.market_topic_id == topic.id).limit(1))
            if not has_member:
                db.delete(topic)
        db.flush()
        videos = db.scalars(select(MarketVideo).where(MarketVideo.shorts_status == "VERIFIED_SHORTS")).all()
        by_token: dict[str, list[MarketVideo]] = defaultdict(list)
        channels_by_token: dict[str, set[str]] = defaultdict(set)
        for video in videos:
            for token in _tokens(video.title):
                by_token[token].append(video)
                if video.channel_id:
                    channels_by_token[token].add(video.channel_id)
        candidates = {token: members for token, members in by_token.items() if len({item.channel_id or item.video_id for item in members}) >= 3 and len(channels_by_token[token]) >= 3}
        existing = {}
        for topic in db.scalars(select(MarketTopic).where(MarketTopic.status == "PROVISIONAL")).all():
            existing.setdefault(topic.label, topic)
        assigned_ids = set(db.scalars(select(MarketTopicMembership.market_video_id)).all())
        created = assigned = 0
        # A video has one primary title-overlap topic. Prefer the most repeated
        # candidate so member counts remain real after the one-membership rule.
        primary: dict[int, str] = {}
        for token, members in candidates.items():
            for video in members:
                previous = primary.get(video.id)
                if previous is None or len(candidates[token]) > len(candidates[previous]):
                    primary[video.id] = token
        for token, members in sorted(candidates.items(), key=lambda item: (-len(item[1]), item[0])):
            selected = [video for video in members if primary.get(video.id) == token]
            if len({video.channel_id or video.video_id for video in selected}) < 3:
                continue
            label = token.title() + LABEL_SUFFIX
            topic = existing.get(label)
            if not topic:
                topic = MarketTopic(label=label, status="PROVISIONAL")
                db.add(topic)
                db.flush()
                existing[label] = topic
                created += 1
            for video in selected:
                if video.id in assigned_ids:
                    continue
                db.add(MarketTopicMembership(market_topic_id=topic.id, market_video_id=video.id, similarity_score=1.0))
                assigned_ids.add(video.id)
                assigned += 1
        db.commit()
      return {"created": created, "assigned": assigned, "candidate_terms": len(candidates)}
    finally:
      store.client.delete(LOCK)
