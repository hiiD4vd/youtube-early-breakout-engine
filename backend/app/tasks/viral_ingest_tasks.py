"""Kirim sinyal YouTube (Y-CGC) ke ViralEngine milik kantor.

Tiga jenis data dikirim tiap run dengan namespace topic_id berbeda:
  youtube:topic:{id}  — Topic Pool (market_ranked_topics, topik tren Shorts)
  youtube:video:{id}  — Video Trends (video umum yang sedang tren)
  youtube:short:{id}  — Shorts Trends (shorts yang sedang naik daun)

Semuanya dikirim ke POST /api/v1/trending/ingest dengan kategori "youtube"
sehingga frontend dashboard kantor dapat memisahkan surface YouTube
dari surface TikTok Studio tanpa mengubah backend mereka.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import desc, func, select

from app.database import SessionLocal
from app.models.market_trends import (
    MarketRankedTopic,
    MarketRankedTopicMembership,
    MarketVideo,
    MarketVideoObservation,
)
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

BASE_URL = os.environ.get("VIRAL_ENGINE_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("VIRAL_ENGINE_API_KEY", "")

INGEST_URL = f"{BASE_URL}/api/v1/trending/ingest" if BASE_URL else ""
LOCK = "ycgc:lock:viral-ingest"
TIMEOUT = httpx.Timeout(20.0)

# Berapa item per jenis yang dikirim tiap run
TOPIC_LIMIT = 60
VIDEO_LIMIT = 40
SHORT_LIMIT = 40

# Minimum view count untuk video/shorts — jangan kirim video gurem
MIN_VIEWS = 1_000

# Kategori yang valid di ViralEngine (harus salah satu daftar resmi mereka,
# "youtube" sudah kita putuskan sebagai penanda surface)
VIRAL_CATEGORY = "youtube"


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def _send(items: list[dict]) -> dict:
    if not items:
        return {"skipped": 0, "status": "empty"}
    payload = {"items": items}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(INGEST_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


def _topic_pool_items(db) -> list[dict]:
    """Topic Pool → topik tren Shorts yang sudah ter-cluster & ter-score."""
    topics = db.scalars(
        select(MarketRankedTopic)
        .where(MarketRankedTopic.status.in_(("THEME", "EMERGING", "ACCELERATING", "CONFIRMED")))
        .order_by(desc(MarketRankedTopic.trend_score))
        .limit(TOPIC_LIMIT)
    ).all()
    items = []
    for rank, topic in enumerate(topics, start=1):
        video_ids = db.scalars(
            select(MarketVideo.video_id)
            .join(
                MarketRankedTopicMembership,
                MarketRankedTopicMembership.market_video_id == MarketVideo.id,
            )
            .where(MarketRankedTopicMembership.market_ranked_topic_id == topic.id)
            .limit(25)
        ).all()
        items.append(
            {
                "topic_id": f"youtube:topic:{topic.id}",
                "region": "ALL",
                "category": VIRAL_CATEGORY,
                "title": topic.label,
                "mentionCount": topic.observed_views or 0,
                "engagementScore": round((topic.trend_score or 0) / 100, 4),
                "rank": rank,
                "relatedPosts": list(video_ids),
            }
        )
    return items


def _video_items(db, *, shorts_only: bool, namespace: str, limit: int) -> list[dict]:
    """Video Trends & Shorts Trends → video per-video dengan velocity terbaru."""
    cutoff = datetime.now(UTC) - timedelta(hours=72)
    # Video Trends hanya video non-shorts (long-form); Shorts Trends hanya shorts.
    # Sebelumnya Video Trends ikut menyertakan VERIFIED_SHORTS sehingga setiap
    # shorts muncul dua kali di dashboard (sebagai video DAN sebagai short).
    statuses = ("VERIFIED_SHORTS",) if shorts_only else ("UNVERIFIED", "REJECTED_NOT_SHORTS")

    # Ambil observasi terbaru per video (7 hari) dengan view_count >= MIN_VIEWS
    rows = db.execute(
        select(
            MarketVideo.id,
            MarketVideo.video_id,
            MarketVideo.title,
            MarketVideo.channel_title,
            MarketVideoObservation.view_count,
            MarketVideoObservation.observed_at,
            MarketVideoObservation.region,
        )
        .join(MarketVideoObservation, MarketVideoObservation.market_video_id == MarketVideo.id)
        .where(
            MarketVideo.shorts_status.in_(statuses),
            MarketVideoObservation.observed_at >= cutoff,
            MarketVideoObservation.view_count >= MIN_VIEWS,
        )
        .order_by(
            MarketVideo.id,
            desc(MarketVideoObservation.observed_at),
        )
        .distinct(MarketVideo.id)
        .order_by(desc(MarketVideoObservation.view_count))
        .limit(limit)
    ).all()

    items = []
    for rank, (vid, video_id, title, channel_title, views, observed_at, region) in enumerate(rows, start=1):
        items.append(
            {
                "topic_id": f"{namespace}:{vid}",
                "region": region or "ALL",
                "category": VIRAL_CATEGORY,
                "title": f"{title or '(tanpa judul)'} — {channel_title or '?'}",
                "mentionCount": views or 0,
                "rank": rank,
                "relatedPosts": [video_id],
            }
        )
    return items


@celery_app.task(name="app.tasks.viral_ingest_tasks.send_youtube_to_viral_engine")
def send_youtube_to_viral_engine() -> dict[str, int | str]:
    """Kirim 3 jenis sinyal YouTube ke ViralEngine (idempotent upsert)."""
    if not BASE_URL or not API_KEY:
        return {"status": "viral_engine_not_configured"}

    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=840):
        return {"status": "skipped_locked"}

    results: dict[str, int | str] = {"status": "ok"}
    try:
        with SessionLocal() as db:
            topic_items = _topic_pool_items(db)
            video_items = _video_items(db, shorts_only=False, namespace="youtube:video", limit=VIDEO_LIMIT)
            short_items = _video_items(db, shorts_only=True, namespace="youtube:short", limit=SHORT_LIMIT)

        results["topics_prepared"] = len(topic_items)
        results["videos_prepared"] = len(video_items)
        results["shorts_prepared"] = len(short_items)

        # Kirim satu payload gabungan (ViralEngine upsert berbasis topic_id)
        all_items = topic_items + video_items + short_items
        if all_items:
            resp = _send(all_items)
            results["sent"] = len(all_items)
            results["ingest_response"] = str(resp)[:200]
        else:
            results["sent"] = 0

        store.set_status(viral_ingest_last_run_at=datetime.now(UTC).isoformat(), viral_ingest_sent=len(all_items))
        return results
    finally:
        store.client.delete(LOCK)
