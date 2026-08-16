"""Cluster-level semantic naming; never called for every individual Short."""
from __future__ import annotations

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.models.market_trends import MarketMetadataTrend, MarketMetadataTrendMembership, MarketVideo
from app.services.gemini_client import GeminiClient
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:market-semantic-topic"
COOLDOWN = "ycgc:youtube:market-gemini-cooldown"

@celery_app.task(name="app.tasks.market_semantic_topic_tasks.name_metadata_clusters")
def name_metadata_clusters() -> dict[str, int | str]:
    store = SeedStore()
    if store.client.exists(COOLDOWN) or not store.client.set(LOCK, "1", nx=True, ex=540): return {"status": "cooldown_or_locked"}
    try:
      with SessionLocal() as db:
        trends = db.scalars(select(MarketMetadataTrend).where(MarketMetadataTrend.semantic_status == "AI_PENDING", MarketMetadataTrend.member_count >= 3).limit(8)).all()
        client = GeminiClient(); updated = 0
        for trend in trends:
          videos = db.scalars(select(MarketVideo).join(MarketMetadataTrendMembership, MarketMetadataTrendMembership.market_video_id == MarketVideo.id).where(MarketMetadataTrendMembership.market_metadata_trend_id == trend.id).limit(8)).all()
          evidence = "\n".join(f"- Title: {video.title or ''}\n  Caption: {(video.description or '')[:500]}" for video in videos)
          try:
            facts = client.analyze_topic_cluster(evidence)
          except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
              store.client.set(COOLDOWN, "1", ex=3600); db.commit(); return {"updated": updated, "status": "quota_cooldown"}
            raise
          trend.semantic_label, trend.semantic_summary, trend.semantic_confidence = facts.topic_title, facts.summary, facts.confidence
          trend.followable = facts.followable and facts.confidence >= .75
          trend.semantic_status = "AI_READY" if trend.followable else "AI_REJECTED"
          trend.status = "METADATA_EMERGING" if trend.followable else "WATCHING"
          updated += 1
        db.commit()
      return {"updated": updated}
    finally: store.client.delete(LOCK)
