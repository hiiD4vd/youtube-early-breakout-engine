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
# Internal Y-CGC API (this container calls its own API to reuse the exact same
# ranking/compute logic that renders the local dashboard, so numbers match).
YCGC_INTERNAL = os.environ.get("YCGC_INTERNAL_URL", "http://backend:8000").rstrip("/")
# Dedicated YouTube ingest endpoint (isolated from TikTok/X studio ingest).
INGEST_YOUTUBE_URL = f"{BASE_URL}/api/v1/trending/ingest-youtube" if BASE_URL else ""
LOCK = "ycgc:lock:viral-ingest"
LOCK_YOUTUBE = "ycgc:lock:viral-ingest-youtube"
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


def _post(video_id: str, title: str | None, channel: str | None, views: int) -> dict:
    """Rich related-post object the ViralEngine ingest parser understands.

    Keys match `_extract_posts` / `_cover_from` in the ViralEngine backend:
      video_id  -> Id/ItemId/video_id
      caption   -> ItemName/title/caption
      cover_url -> CoverUrl/cover_url
      play_count-> PlayCount/play_count
    Thumbnail is derived from video_id (YouTube CDN) so no backend change
    and no extra API call is needed.
    """
    return {
        "video_id": video_id,
        "caption": f"{title or '(tanpa judul)'} — {channel or '?'}",
        "title": title or "(tanpa judul)",
        "cover_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "play_count": int(views or 0),
        "like_count": 0,
    }


def _thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


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
        members = db.execute(
            select(
                MarketVideo.video_id,
                MarketVideo.title,
                MarketVideo.channel_title,
                MarketVideoObservation.view_count,
            )
            .select_from(MarketRankedTopicMembership)
            .join(MarketVideo, MarketVideo.id == MarketRankedTopicMembership.market_video_id)
            .outerjoin(
                MarketVideoObservation,
                MarketVideoObservation.market_video_id == MarketVideo.id,
            )
            .where(MarketRankedTopicMembership.market_ranked_topic_id == topic.id)
            .order_by(desc(MarketVideoObservation.view_count))
            .limit(25)
        ).all()
        related_posts = [
            _post(mvid, mtitle, mchannel, mviews) for (mvid, mtitle, mchannel, mviews) in members
        ]
        # Keep only the bare ids too (related_video_ids) so existing surfaces keep working
        video_ids = [mv_id for (mv_id, _t, _c, _v) in members]
        items.append(
            {
                "topic_id": f"youtube:topic:{topic.id}",
                "region": "ALL",
                "category": VIRAL_CATEGORY,
                "title": topic.label,
                "mentionCount": topic.observed_views or 0,
                "engagementScore": round((topic.trend_score or 0) / 100, 4),
                "rank": rank,
                "relatedPosts": related_posts,
                "relatedVideoIds": video_ids,
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
                "relatedPosts": [_post(video_id, title, channel_title, views)],
                "relatedVideoIds": [video_id],
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


def _map_topic_to_v2(item: dict) -> dict:
    """Map topic-pool /topic item to the V2 rich payload youtube_ingest reads."""
    members = item.get("members") or []
    member_ids = []
    for m in members:
        vid = m.get("video_id") or m.get("id")
        if vid:
            member_ids.append(str(vid))
    return {
        "topic_id": f"youtube:topic:{item.get('id') or item.get('topic_id') or item.get('label')}",
        "surface": "topic",
        "region": (item.get("region") or "ALL").upper(),
        "category": VIRAL_CATEGORY,
        "title": item.get("label") or item.get("title"),
        "rank": item.get("rank"),
        "mention_count": int(item.get("observed_views") or item.get("mention_count") or 0),
        "observed_views": int(item.get("observed_views") or 0),
        "period_growth_views": int(item.get("period_growth_views") or 0),
        "observed_velocity_per_hour": float(item.get("observed_velocity_per_hour") or 0),
        "organic_velocity_per_hour": float(item.get("organic_velocity_per_hour") or int(item.get("velocity_per_hour") or 0)),
        "ranking_score": item.get("ranking_score") or item.get("trend_score"),
        "ranking_reason": item.get("ranking_reason"),
        "member_count": int(item.get("member_count") or len(members) or 0),
        "channel_count": int(item.get("channel_count") or 0),
        "media_mix": item.get("media_mix") or {},
        "members": members,
        "related_video_ids": member_ids,
    }


def _map_video_to_v2(item: dict, surface: str) -> dict:
    """Map a video/short trend item to the V2 rich payload youtube_ingest reads."""
    video_id = item.get("video_id") or item.get("id")
    title = item.get("title") or ""
    channel = item.get("channel_title") or ""
    regions = item.get("tracked_regions") or item.get("region") or ["ALL"]
    if isinstance(regions, str):
        regions = [regions]
    return {
        "topic_id": f"youtube:{surface}:{video_id}",
        "surface": surface,
        "region": (regions[0] if regions else "ALL").upper(),
        "category": VIRAL_CATEGORY,
        "title": f"{title} — {channel}" if channel else title,
        "rank": item.get("rank"),
        "mention_count": int(item.get("view_count") or 0),
        "observed_views": int(item.get("view_count") or 0),
        "period_growth_views": int(item.get("views_gained") or 0),
        "observed_velocity_per_hour": float(item.get("velocity_per_hour") or 0),
        "organic_velocity_per_hour": float(item.get("velocity_per_hour") or 0),
        "ranking_score": item.get("rank_change"),
        "ranking_reason": None,
        "member_count": int(item.get("observation_count") or 0),
        "channel_count": 1,
        "media_mix": {},
        "members": [
            {
                "video_id": video_id,
                "title": title,
                "channel_title": channel,
                "thumbnail_url": item.get("thumbnail_url"),
                "cover_url": item.get("thumbnail_url") or (f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None),
                "current_view_count": item.get("view_count"),
                "view_count": item.get("view_count"),
                "velocity_per_hour": item.get("velocity_per_hour"),
            }
        ],
        "related_video_ids": [video_id] if video_id else [],
    }


def _send_youtube(items: list[dict]) -> dict:
    if not items:
        return {"skipped": 0, "status": "empty"}
    payload = {"items": items}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(INGEST_YOUTUBE_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


@celery_app.task(name="app.tasks.viral_ingest_tasks.send_youtube_signals_rich")
def send_youtube_signals_rich() -> dict:
    """Forward rich YouTube signals (reusing the local API's exact compute logic)
    to ViralEngine's isolated /trending/ingest-youtube endpoint.

    Instead of re-deriving ranking/velocity here (risky to get subtly wrong),
    we call Y-CGC's own endpoints — the same code that renders the local
    dashboard — and forward the payload. This guarantees numbers match.
    """
    if not BASE_URL or not API_KEY or not YCGC_INTERNAL:
        return {"status": "youtube_engine_not_configured"}

    store = SeedStore()
    if not store.client.set(LOCK_YOUTUBE, "1", nx=True, ex=840):
        return {"status": "skipped_locked"}

    results: dict[str, int | str] = {"status": "ok"}
    try:
        # Collect via each surface endpoint (all use the local API's compute).
        endpoints = [
            ("topic", f"{YCGC_INTERNAL}/api/v1/youtube/topic-pool?limit={TOPIC_LIMIT}"),
            ("video", f"{YCGC_INTERNAL}/api/v1/youtube/video-trends?limit={VIDEO_LIMIT}&sort=rank&period_days=7"),
            ("short", f"{YCGC_INTERNAL}/api/v1/youtube/shorts-trends?limit={SHORT_LIMIT}&sort=rank&period_days=7"),
        ]
        items_v2: list[dict] = []
        with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
            for surface, url in endpoints:
                r = client.get(url, headers={"Content-Type": "application/json"})
                r.raise_for_status()
                data = r.json()
                raw_items = data.get("items", [])
                mapped = []
                for it in raw_items:
                    if surface == "topic":
                        mapped.append(_map_topic_to_v2(it))
                    else:
                        mapped.append(_map_video_to_v2(it, surface))
                items_v2.extend(mapped)
                results[f"{surface}_prepared"] = len(mapped)

        if items_v2:
            resp = _send_youtube(items_v2)
            results["sent"] = len(items_v2)
            results["ingest_response"] = str(resp)[:200]
        else:
            results["sent"] = 0

        store.set_status(viral_ingest_youtube_last_run_at=datetime.now(UTC).isoformat(), viral_ingest_youtube_sent=len(items_v2))
        return results
    finally:
        store.client.delete(LOCK_YOUTUBE)
