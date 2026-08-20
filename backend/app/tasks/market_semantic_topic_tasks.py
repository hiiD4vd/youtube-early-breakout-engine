"""Cluster-level semantic naming; never called for every individual Short."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256

import httpx
from sqlalchemy import desc, select

from app.database import SessionLocal
from app.models.market_trends import (
    MarketMetadataTrend,
    MarketMetadataTrendMembership,
    MarketMetadataTrendSnapshot,
    MarketVideo,
    MarketVideoFeature,
    MarketVideoObservation,
)
from app.services.market_semantic_client import MarketSemanticClient
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:market-semantic-topic"
COOLDOWN = "ycgc:youtube:market-semantic-cluster-cooldown"
RECOVERY_LOCK = "ycgc:youtube:lock:market-rejected-theme-recovery"

GENERIC_THEME_TOKENS = {
    "short", "shorts", "video", "videos", "viral", "funny", "content",
    "entertainment", "music", "sport", "sports", "clip", "clips",
    "moment", "moments", "trending",
}


def _theme_identity(semantic: dict) -> tuple[str, str] | None:
    """Return a stable, human topic identity from an existing per-video review.

    This is deliberately local: rejected metadata candidates can be mined
    without paying for another model request.  The final candidate still goes
    through the normal cluster-level reviewer before public promotion.
    """
    theme = str(semantic.get("topic_theme") or "").strip()
    content_format = str(semantic.get("content_format") or "").strip()
    combined = f"{theme} {content_format}".casefold()
    if any(token in combined for token in ("ranking", "countdown", "top list", "ranked list")):
        return "format-ranking", "Video ranking dan hitung mundur"
    if any(token in combined for token in ("what happens if", "what-if", "apa yang terjadi jika")):
        return "format-what-if", "Eksperimen 'apa yang terjadi jika'"
    try:
        confidence = float(semantic.get("theme_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < .60 or len(theme) < 8:
        return None
    tokens = [
        token for token in re.findall(r"[\w']+", theme.casefold())
        if len(token) > 2 and token not in GENERIC_THEME_TOKENS
    ]
    if len(tokens) < 2:
        return None
    return "theme-" + "-".join(sorted(set(tokens))), theme[:160]


@celery_app.task(name="app.tasks.market_semantic_topic_tasks.recover_rejected_semantic_themes")
def recover_rejected_semantic_themes() -> dict[str, int | str]:
    """Find real shared themes hidden inside rejected keyword candidates.

    A metadata candidate may be rejected because one hashtag/name mixed many
    unrelated videos. Its members are not discarded. Once individual videos
    have semantic fingerprints, this pass regroups them across *all* rejected
    candidates and creates as many coherent multi-channel candidates as the
    evidence supports.
    """
    store = SeedStore()
    if not store.client.set(RECOVERY_LOCK, "1", nx=True, ex=840):
        return {"status": "skipped_locked"}
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(MarketVideo, MarketVideoFeature)
                .join(MarketMetadataTrendMembership, MarketMetadataTrendMembership.market_video_id == MarketVideo.id)
                .join(MarketMetadataTrend, MarketMetadataTrend.id == MarketMetadataTrendMembership.market_metadata_trend_id)
                .join(MarketVideoFeature, MarketVideoFeature.market_video_id == MarketVideo.id)
                .where(
                    MarketMetadataTrend.semantic_status.in_(("AI_REJECTED", "AI_REJECTED_V2")),
                    MarketVideoFeature.feature_model == "market-semantic-v6",
                )
            ).all()
            # The same video can occur in many rejected hashtag/entity groups.
            # Count it once before constructing semantic themes.
            unique_rows = {video.id: (video, feature) for video, feature in rows}
            grouped: dict[str, list[tuple[MarketVideo, MarketVideoFeature, str]]] = defaultdict(list)
            for video, feature in unique_rows.values():
                semantic = (feature.provenance or {}).get("semantic")
                if not isinstance(semantic, dict):
                    continue
                identity = _theme_identity(semantic)
                if identity:
                    key, label = identity
                    grouped[key].append((video, feature, label))

            latest: dict[int, MarketVideoObservation] = {}
            for observation in db.scalars(
                select(MarketVideoObservation).order_by(desc(MarketVideoObservation.observed_at))
            ).all():
                latest.setdefault(observation.market_video_id, observation)

            created = updated = memberships_added = 0
            now = datetime.now(UTC)
            for identity, members in grouped.items():
                channels = {video.channel_id or video.video_id for video, _feature, _label in members}
                if len(members) < 2 or len(channels) < 2:
                    continue
                label = members[0][2]
                signal_key = "semantic-theme:" + sha256(identity.encode()).hexdigest()[:24]
                trend = db.scalar(select(MarketMetadataTrend).where(MarketMetadataTrend.signal_key == signal_key))
                if trend is None:
                    trend = MarketMetadataTrend(
                        signal_key=signal_key,
                        label=label,
                        signal_type="SEMANTIC_THEME",
                        semantic_status="AI_PENDING",
                    )
                    db.add(trend)
                    db.flush()
                    created += 1
                else:
                    updated += 1
                regions = {
                    latest[video.id].region for video, _feature, _label in members
                    if video.id in latest and latest[video.id].region
                }
                trend.label = label
                trend.member_count = len(members)
                trend.channel_count = len(channels)
                trend.region_count = len(regions)
                trend.last_observed_at = now
                trend.status = "WATCHING" if trend.semantic_status != "AI_READY" else "METADATA_EMERGING"
                known = set(db.scalars(
                    select(MarketMetadataTrendMembership.market_video_id)
                    .where(MarketMetadataTrendMembership.market_metadata_trend_id == trend.id)
                ).all())
                for video, _feature, _label in members:
                    if video.id not in known:
                        db.add(MarketMetadataTrendMembership(
                            market_metadata_trend_id=trend.id,
                            market_video_id=video.id,
                            matched_term=identity[:255],
                        ))
                        memberships_added += 1
                db.add(MarketMetadataTrendSnapshot(
                    market_metadata_trend_id=trend.id,
                    observed_at=now,
                    member_count=len(members),
                    channel_count=len(channels),
                    region_count=len(regions),
                    fresh_ratio=trend.fresh_ratio,
                    burst_score=trend.burst_score,
                ))
            db.commit()
            return {
                "status": "ok",
                "rejected_members_seen": len(unique_rows),
                "coherent_themes": sum(1 for members in grouped.values() if len(members) >= 2),
                "created": created,
                "updated": updated,
                "memberships_added": memberships_added,
            }
    finally:
        store.client.delete(RECOVERY_LOCK)

@celery_app.task(name="app.tasks.market_semantic_topic_tasks.name_metadata_clusters")
def name_metadata_clusters() -> dict[str, int | str]:
    store = SeedStore()
    if store.client.exists(COOLDOWN) or not store.client.set(LOCK, "1", nx=True, ex=540): return {"status": "cooldown_or_locked"}
    try:
      with SessionLocal() as db:
        trends = db.scalars(select(MarketMetadataTrend).where(MarketMetadataTrend.semantic_status == "AI_PENDING", MarketMetadataTrend.member_count >= 3).limit(8)).all()
        client = MarketSemanticClient(); updated = 0; errors = 0
        for trend in trends:
          videos = db.scalars(select(MarketVideo).join(MarketMetadataTrendMembership, MarketMetadataTrendMembership.market_video_id == MarketVideo.id).where(MarketMetadataTrendMembership.market_metadata_trend_id == trend.id).limit(8)).all()
          evidence = "\n".join(f"- Title: {video.title or ''}\n  Caption: {(video.description or '')[:500]}" for video in videos)
          try:
            facts = client.review_topic_cluster(evidence)
          except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
              store.client.set(COOLDOWN, "1", ex=3600); db.commit(); return {"updated": updated, "status": "quota_cooldown"}
            errors += 1
            continue
          except RuntimeError:
            # One malformed provider response must not discard reviews that
            # already succeeded in the same batch. Keep this candidate pending
            # so a later scheduled run can retry it.
            errors += 1
            continue
          trend.semantic_label, trend.semantic_summary, trend.semantic_confidence = facts.topic_title, facts.summary, facts.confidence
          trend.followable = facts.followable and facts.confidence >= .75
          trend.semantic_status = "AI_READY" if trend.followable else "AI_REJECTED"
          trend.status = "METADATA_EMERGING" if trend.followable else "WATCHING"
          updated += 1
        db.commit()
      return {"updated": updated, "errors": errors}
    finally: store.client.delete(LOCK)
