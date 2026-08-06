"""Post-signal feature collection and provisional topic-candidate clustering."""

from __future__ import annotations

from datetime import UTC, datetime

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.trend_cluster import TrendCluster, TrendMembership, TrendSignalFeature
from app.models.youtube_snipe import YoutubeSnipe
from app.services.seed_store import SeedStore
from app.services.trend_features import build_feature_payload, cosine_similarity, provisional_label
from app.tasks.celery_app import celery_app

FEATURE_LOCK = "ycgc:youtube:lock:trend-features"
CLUSTER_LOCK = "ycgc:youtube:lock:trend-clustering"
ACTIVE_TIERS = ("EARLY", "RISING", "BREAKOUT")


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.build_trend_signal_features", soft_time_limit=300, time_limit=360)
def build_trend_signal_features(self: Task) -> dict[str, int | str]:
    store = SeedStore()
    if not store.client.set(FEATURE_LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked"}
    created = updated = skipped = 0
    try:
        with SessionLocal() as db:
            signals = db.scalars(select(YoutubeSnipe).where(YoutubeSnipe.signal_tier.in_(ACTIVE_TIERS))).all()
            existing = {item.youtube_snipe_id: item for item in db.scalars(select(TrendSignalFeature)).all()}
            for signal in signals:
                payload = build_feature_payload(signal)
                feature = existing.get(signal.id)
                if feature and feature.content_hash == payload["content_hash"] and feature.feature_model == payload["feature_model"]:
                    skipped += 1
                    continue
                if feature:
                    for key, value in payload.items():
                        setattr(feature, key, value)
                    updated += 1
                else:
                    db.add(TrendSignalFeature(youtube_snipe_id=signal.id, **payload))
                    created += 1
            db.commit()
        store.set_status(last_trend_feature_run_at=datetime.now(UTC).isoformat(), last_trend_features_created=created, last_trend_features_updated=updated)
        return {"created": created, "updated": updated, "skipped": skipped}
    finally:
        store.client.delete(FEATURE_LOCK)


def _cluster_vector(db, cluster_id) -> dict[str, float]:
    features = db.execute(
        select(TrendSignalFeature.sparse_vector)
        .join(TrendMembership, TrendMembership.youtube_snipe_id == TrendSignalFeature.youtube_snipe_id)
        .where(TrendMembership.cluster_id == cluster_id)
    ).scalars().all()
    aggregate: dict[str, float] = {}
    for vector in features:
        for token, weight in (vector or {}).items():
            aggregate[token] = aggregate.get(token, 0) + float(weight)
    norm = sum(value * value for value in aggregate.values()) ** 0.5
    return {token: value / norm for token, value in aggregate.items()} if norm else {}


@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.cluster_recent_signals", soft_time_limit=300, time_limit=360)
def cluster_recent_signals(self: Task) -> dict[str, int | str]:
    """Make private, reversible candidate clusters. Public ranking comes in T2."""
    store = SeedStore()
    if not store.client.set(CLUSTER_LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked"}
    created = assigned = 0
    try:
        with SessionLocal() as db:
            signals = db.scalars(select(YoutubeSnipe).where(YoutubeSnipe.signal_tier.in_(ACTIVE_TIERS)).order_by(YoutubeSnipe.detected_at)).all()
            features = {item.youtube_snipe_id: item for item in db.scalars(select(TrendSignalFeature)).all()}
            member_signal_ids = set(db.scalars(select(TrendMembership.youtube_snipe_id)).all())
            clusters = db.scalars(select(TrendCluster).where(TrendCluster.status == "PRIVATE_CANDIDATE")).all()
            vectors = {cluster.id: _cluster_vector(db, cluster.id) for cluster in clusters}
            for signal in signals:
                if signal.id in member_signal_ids:
                    continue
                feature = features.get(signal.id)
                if not feature or not feature.sparse_vector:
                    continue
                best = None
                best_score = 0.0
                for cluster in clusters:
                    score = cosine_similarity(feature.sparse_vector, vectors.get(cluster.id))
                    if cluster.niche and signal.niche and cluster.niche.casefold() == signal.niche.casefold():
                        score = min(1.0, score + 0.08)
                    if score > best_score:
                        best, best_score = cluster, score
                if best is None or best_score < settings.topic_lexical_similarity_threshold:
                    best = TrendCluster(
                        public_slug=f"candidate-{signal.video_id.lower()}",
                        label=provisional_label(feature.sparse_vector),
                        label_confidence=feature.confidence,
                        niche=signal.niche,
                        status="PRIVATE_CANDIDATE",
                        cluster_reason="single signal candidate; awaits independent cross-channel evidence",
                        model_metadata={"feature_model": feature.feature_model, "clustering_version": "lexical-v1"},
                    )
                    db.add(best)
                    db.flush()
                    clusters.append(best)
                    vectors[best.id] = dict(feature.sparse_vector)
                    created += 1
                    best_score = 1.0
                existing_channels = set(db.scalars(select(YoutubeSnipe.channel_id).join(TrendMembership, TrendMembership.youtube_snipe_id == YoutubeSnipe.id).where(TrendMembership.cluster_id == best.id)).all())
                same_channel = signal.channel_id in existing_channels
                db.add(TrendMembership(
                    cluster_id=best.id,
                    youtube_snipe_id=signal.id,
                    similarity_score=best_score,
                    membership_state="PROVISIONAL",
                    is_same_channel_duplicate=same_channel,
                    weight=0.25 if same_channel else 1.0,
                    feature_evidence={"feature_model": feature.feature_model, "similarity": best_score, "source_provenance": feature.source_provenance},
                ))
                best.member_count += 1
                best.channel_count = len(existing_channels | {signal.channel_id})
                best.observed_views += signal.current_view_count
                best.observed_velocity_per_hour += signal.velocity_per_hour
                best.last_member_at = datetime.now(UTC)
                best.last_observed_at = datetime.now(UTC)
                assigned += 1
            db.commit()
        store.set_status(last_trend_cluster_run_at=datetime.now(UTC).isoformat(), last_trend_candidates_created=created, last_trend_memberships_assigned=assigned)
        return {"candidates_created": created, "memberships_assigned": assigned}
    finally:
        store.client.delete(CLUSTER_LOCK)
